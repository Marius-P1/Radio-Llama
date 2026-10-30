import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from radio_llama.audio import (
    AudioError,
    assemble_show,
    check_order,
    concat_parts,
    normalize_voice,
    prepare_song,
    probe_duration,
)
from radio_llama.audio.ffmpeg import run_ffmpeg
from radio_llama.models import Segment, SegmentKind, Show, ShowConfig

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

TARGET_LUFS = -16.0
LOUDNESS_TOLERANCE_LU = 3.0


def _tone(path: Path, seconds: float, frequency: float = 440.0, volume: str | None = None) -> None:
    """Write a sine tone as MP3. `volume` is an ffmpeg volume filter value such as "-30dB"."""
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i"]
    args.append(f"sine=frequency={frequency}:duration={seconds}")
    if volume is not None:
        args += ["-af", f"volume={volume}"]
    args += ["-c:a", "libmp3lame", "-q:a", "4", str(path)]
    subprocess.run(args, check=True)


def _loudness(path: Path) -> float:
    """Integrated loudness in LUFS, measured by ffmpeg's loudnorm filter."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stderr = result.stderr
    block = stderr[stderr.rfind("{") : stderr.rfind("}") + 1]
    return float(json.loads(block)["input_i"])


def _segment(kind: SegmentKind) -> Segment:
    return Segment(kind=kind, text=None if kind == "song" else f"{kind} text")


def _show(kinds: list[SegmentKind], song_length_s: float = 2.0) -> Show:
    return Show(
        id="test-show",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        config=ShowConfig(style="jazz", persona="calm", song_length_s=song_length_s),
        segments=[_segment(kind) for kind in kinds],
    )


def test_prepare_song_trims_fades_and_normalises(tmp_path: Path) -> None:
    src = tmp_path / "tone.mp3"
    dst = tmp_path / "song.mp3"
    _tone(src, 10.0)

    prepare_song(src, dst, length_s=4.0)

    assert abs(probe_duration(dst) - 4.0) <= 0.3
    loudness = _loudness(dst)
    print(f"prepare_song loudness: {loudness:.2f} LUFS")
    assert abs(loudness - TARGET_LUFS) <= LOUDNESS_TOLERANCE_LU


def test_prepare_song_shorter_than_fade_out_does_not_fail(tmp_path: Path) -> None:
    src = tmp_path / "tone.mp3"
    dst = tmp_path / "song.mp3"
    _tone(src, 10.0)

    prepare_song(src, dst, length_s=2.0)

    assert abs(probe_duration(dst) - 2.0) <= 0.3


def test_normalize_voice_brings_quiet_speech_to_target(tmp_path: Path) -> None:
    src = tmp_path / "quiet.mp3"
    dst = tmp_path / "voice.mp3"
    _tone(src, 3.0, volume="-30dB")
    before = _loudness(src)
    assert before < TARGET_LUFS - LOUDNESS_TOLERANCE_LU, "the source must start quiet"

    normalize_voice(src, dst)

    after = _loudness(dst)
    print(f"normalize_voice loudness: {before:.2f} -> {after:.2f} LUFS")
    assert abs(after - TARGET_LUFS) <= LOUDNESS_TOLERANCE_LU


def test_concat_parts_joins_files_end_to_end(tmp_path: Path) -> None:
    parts = []
    for index in range(3):
        part = tmp_path / f"part{index}.mp3"
        _tone(part, 1.0, frequency=440.0 * (index + 1))
        parts.append(part)
    dst = tmp_path / "joined.mp3"

    concat_parts(parts, dst)

    assert abs(probe_duration(dst) - 3.0) <= 0.3
    assert not dst.with_suffix(".txt").exists()


def test_check_order_accepts_a_valid_show() -> None:
    check_order(_show(["intro", "song", "transition", "song", "outro"]).segments)


@pytest.mark.parametrize(
    ("kinds", "problem"),
    [
        (["song", "outro"], "intro"),
        (["intro", "song", "outro", "outro"], "outro"),
        (["intro", "song", "song", "outro"], "consecutive"),
        (["intro", "transition", "outro"], "song"),
    ],
)
def test_check_order_rejects_bad_sequences(kinds: list[SegmentKind], problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        check_order(_show(kinds).segments)


def test_assemble_show_writes_parts_mix_and_json(tmp_path: Path) -> None:
    kinds: list[SegmentKind] = ["intro", "song", "transition", "song", "outro"]
    show = _show(kinds, song_length_s=2.0)
    # Pure digital silence makes loudnorm emit NaN and libmp3lame abort, so speech
    # fixtures are a faint tone. Real TTS output is never fully silent.
    sources = []
    for index, kind in enumerate(kinds):
        src = tmp_path / f"src{index}.mp3"
        if kind == "song":
            _tone(src, 5.0)
        else:
            _tone(src, 1.0, frequency=220.0, volume="-30dB")
        sources.append(src)
    out_dir = tmp_path / "out"

    result = assemble_show(show, sources, out_dir)

    expected = ["00-intro.mp3", "01-song.mp3", "02-transition.mp3", "03-song.mp3", "04-outro.mp3"]
    assert sorted(p.name for p in out_dir.iterdir()) == sorted([*expected, "show.mp3", "show.json"])
    assert [segment.audio_file for segment in result.segments] == expected
    for segment in result.segments:
        assert segment.duration_s is not None and segment.duration_s > 0
    assert result.audio_file == "show.mp3"
    assert result.status == "pending"

    total = sum(segment.duration_s or 0.0 for segment in result.segments)
    assert abs(probe_duration(out_dir / "show.mp3") - total) <= 0.5

    saved = Show.model_validate_json((out_dir / "show.json").read_text(encoding="utf-8"))
    assert saved == result


def test_run_ffmpeg_reports_failure(tmp_path: Path) -> None:
    with pytest.raises(AudioError, match="ffmpeg failed"):
        run_ffmpeg(["-i", str(tmp_path / "missing.mp3"), str(tmp_path / "out.mp3")])
