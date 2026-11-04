import json
import re
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
from radio_llama.audio.assemble import DEMO_SONG_LENGTH_S
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


def _mean_volume(path: Path, start_s: float, length_s: float) -> float:
    """Mean volume in dB of one slice of a file: trim in the filter, then measure what is left.

    Decoding the whole file and trimming in the filter is exact. An input seek (`-ss`)
    on an MP3 is not: the first frame after the seek decodes muted and the slice reads
    too quiet.
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"atrim=start={start_s}:duration={length_s},volumedetect",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"mean_volume: (-?[\d.]+) dB", result.stderr)
    assert match is not None, result.stderr
    return float(match.group(1))


def _segment(kind: SegmentKind) -> Segment:
    return Segment(kind=kind, text=None if kind == "song" else f"{kind} text")


def _show(kinds: list[SegmentKind], demo: bool = False) -> Show:
    return Show(
        id="test-show",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        config=ShowConfig(style="jazz", persona="calm", demo=demo),
        segments=[_segment(kind) for kind in kinds],
    )


def test_prepare_song_full_keeps_the_source_length_and_fades(tmp_path: Path) -> None:
    src = tmp_path / "tone.mp3"
    dst = tmp_path / "song.mp3"
    _tone(src, 10.0)

    prepare_song(src, dst, demo=False)

    assert abs(probe_duration(dst) - 10.0) <= 0.3
    # The tone is constant, so a quieter start and end can only come from the fades.
    middle = _mean_volume(dst, 5.0, 1.0)
    assert _mean_volume(dst, 0.0, 0.3) < middle - 10.0
    assert _mean_volume(dst, 9.5, 0.5) < middle - 10.0


def test_prepare_song_demo_cuts_a_long_song_and_normalises(tmp_path: Path) -> None:
    src = tmp_path / "tone.mp3"
    dst = tmp_path / "song.mp3"
    _tone(src, DEMO_SONG_LENGTH_S + 5.0)

    prepare_song(src, dst, demo=True)

    assert abs(probe_duration(dst) - DEMO_SONG_LENGTH_S) <= 0.3
    loudness = _loudness(dst)
    print(f"prepare_song loudness: {loudness:.2f} LUFS")
    assert abs(loudness - TARGET_LUFS) <= LOUDNESS_TOLERANCE_LU


def test_prepare_song_shorter_than_fade_out_does_not_fail(tmp_path: Path) -> None:
    src = tmp_path / "tone.mp3"
    dst = tmp_path / "song.mp3"
    _tone(src, 2.0)

    prepare_song(src, dst, demo=False)

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


def test_concat_parts_handles_a_quote_in_the_path(tmp_path: Path) -> None:
    folder = tmp_path / "it's here"
    folder.mkdir()
    part = folder / "part.mp3"
    _tone(part, 1.0)
    dst = folder / "joined.mp3"

    concat_parts([part, part], dst)

    assert abs(probe_duration(dst) - 2.0) <= 0.3


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


def test_assemble_show_writes_parts_and_mix(tmp_path: Path) -> None:
    kinds: list[SegmentKind] = ["intro", "song", "transition", "song", "outro"]
    show = _show(kinds)
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
    assert sorted(p.name for p in out_dir.iterdir()) == sorted([*expected, "show.mp3"])
    assert [segment.audio_file for segment in result.segments] == expected
    for segment in result.segments:
        assert segment.duration_s is not None and segment.duration_s > 0
        if segment.kind == "song":
            assert abs(segment.duration_s - 5.0) <= 0.3  # not a demo: the whole source plays
    assert result.audio_file == "show.mp3"
    assert result.status == "pending"

    total = sum(segment.duration_s or 0.0 for segment in result.segments)
    assert abs(probe_duration(out_dir / "show.mp3") - total) <= 0.5


def test_assemble_show_in_demo_mode_cuts_the_song(tmp_path: Path) -> None:
    kinds: list[SegmentKind] = ["intro", "song", "outro"]
    show = _show(kinds, demo=True)
    sources = []
    for index, kind in enumerate(kinds):
        src = tmp_path / f"src{index}.mp3"
        if kind == "song":
            _tone(src, DEMO_SONG_LENGTH_S + 5.0)
        else:
            _tone(src, 1.0, frequency=220.0, volume="-30dB")
        sources.append(src)

    result = assemble_show(show, sources, tmp_path / "out")

    intro, song, outro = result.segments
    assert song.duration_s is not None and abs(song.duration_s - DEMO_SONG_LENGTH_S) <= 0.3
    for voice in (intro, outro):
        assert voice.duration_s is not None and abs(voice.duration_s - 1.0) <= 0.3


def test_run_ffmpeg_reports_failure(tmp_path: Path) -> None:
    with pytest.raises(AudioError, match="ffmpeg failed"):
        run_ffmpeg(["-i", str(tmp_path / "missing.mp3"), str(tmp_path / "out.mp3")])
