"""Turn a voiced show into numbered segment files and one `show.mp3`.

The input is a `Show` whose segments are in final order plus one raw audio file per
segment: the TTS output for speech, the catalog file for a song. The output folder gets
one normalised MP3 per segment (`00-intro.mp3`, `01-song.mp3`, ...) and the joined
`show.mp3`; the segments get their file names and durations filled in. The generator,
not this module, writes `show.json`. Three ffmpeg steps do the work (D014): songs are
faded in and out, and cut to `DEMO_SONG_LENGTH_S` with `-t` when the show is a demo
(D023); every piece is loudness-normalised with `loudnorm` and resampled to 44.1 kHz
stereo; then the pieces are joined with the concat demuxer and encoded to MP3 once. The
single-pass `loudnorm` is deliberate: a two-pass run would double the runtime for a
gain in accuracy a radio show of this kind does not need.
"""

from pathlib import Path

from radio_llama.audio.ffmpeg import probe_duration, run_ffmpeg
from radio_llama.models import Segment, Show

SAMPLE_RATE = 44100  # every piece is resampled to this rate so the concat demuxer sees one format
CHANNELS = 2  # stereo everywhere, for the same reason
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"  # single-pass EBU R128 to -16 LUFS, true peak -1.5 dB
FADE_IN_S = 1.0  # song fade-in length in seconds
FADE_OUT_S = 3.0  # song fade-out length in seconds, ending where the song ends
DEMO_SONG_LENGTH_S = 30.0  # a demo show cuts each song here; a normal show plays it whole
MP3_QUALITY = "4"  # libmp3lame VBR quality, about 165 kbps

_ENCODE = ["-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-c:a", "libmp3lame", "-q:a", MP3_QUALITY]


def normalize_voice(src: Path, dst: Path) -> None:
    """Loudness-normalise a speech file and write it as 44.1 kHz stereo MP3."""
    run_ffmpeg(["-i", str(src), "-af", LOUDNORM, *_ENCODE, str(dst)])


def prepare_song(src: Path, dst: Path, demo: bool) -> None:
    """Fade a song in and out, normalise it and write it as MP3; cut it short in a demo.

    The fade-out ends where the song ends: at the cut in demo mode, at the end of the
    track otherwise, which is why a full song is probed for its length first. Cut,
    fades and loudnorm run in one ffmpeg call with loudnorm last so the fades survive
    the normalisation.
    """
    cut = ["-t", str(DEMO_SONG_LENGTH_S)] if demo else []
    length_s = DEMO_SONG_LENGTH_S if demo else probe_duration(src)
    fade_out_start = max(length_s - FADE_OUT_S, 0.0)
    filters = ",".join(
        [
            f"afade=t=in:st=0:d={FADE_IN_S}",
            f"afade=t=out:st={fade_out_start}:d={FADE_OUT_S}",
            LOUDNORM,
        ]
    )
    run_ffmpeg(["-i", str(src), *cut, "-af", filters, *_ENCODE, str(dst)])


def concat_parts(parts: list[Path], dst: Path) -> None:
    """Join `parts` in order into one MP3 at `dst` with the concat demuxer."""
    list_file = dst.with_suffix(".txt")
    lines = [f"file '{_concat_path(part)}'" for part in parts]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c:a",
                "libmp3lame",
                "-q:a",
                MP3_QUALITY,
                str(dst),
            ]
        )
    finally:
        list_file.unlink(missing_ok=True)


def _concat_path(path: Path) -> str:
    """Absolute path with forward slashes, escaped for the concat demuxer.

    The list file wraps each path in single quotes. A quote inside the path has to close
    the string, add a backslash-escaped quote, and reopen it: a lone quote becomes
    quote, backslash, quote, quote.
    """
    return path.resolve().as_posix().replace("'", "'\\''")


def check_order(segments: list[Segment]) -> None:
    """Raise ValueError unless the segments form a valid show.

    Valid means: starts with one intro, ends with one outro, has at least one song, and
    never plays two segments of the same kind back to back. This is what stops an outro
    from playing twice.
    """
    kinds = [segment.kind for segment in segments]
    if not kinds:
        raise ValueError("show has no segments")
    if kinds[0] != "intro":
        raise ValueError(f"first segment must be an intro, got {kinds[0]!r}")
    if kinds[-1] != "outro":
        raise ValueError(f"last segment must be an outro, got {kinds[-1]!r}")
    if kinds.count("intro") != 1:
        raise ValueError(f"show must have exactly one intro, got {kinds.count('intro')}")
    if kinds.count("outro") != 1:
        raise ValueError(f"show must have exactly one outro, got {kinds.count('outro')}")
    if "song" not in kinds:
        raise ValueError("show must have at least one song")
    for index in range(1, len(kinds)):
        if kinds[index] == kinds[index - 1]:
            raise ValueError(
                f"two consecutive {kinds[index]!r} segments at positions {index - 1} and {index}"
            )


def assemble_show(show: Show, sources: list[Path], out_dir: Path) -> Show:
    """Build the per-segment files and `show.mp3` in `out_dir`.

    `sources[i]` is the raw audio for `show.segments[i]`. The show is updated in place
    (segment `audio_file` and `duration_s`, then `show.audio_file`) and returned. The
    status is left alone and nothing is written besides audio; the caller owns the
    lifecycle and `show.json`.
    """
    check_order(show.segments)
    if len(sources) != len(show.segments):
        raise ValueError(f"{len(sources)} sources for {len(show.segments)} segments")
    out_dir.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []
    for index, (segment, source) in enumerate(zip(show.segments, sources, strict=True)):
        part = out_dir / f"{index:02d}-{segment.kind}.mp3"
        if segment.kind == "song":
            prepare_song(source, part, show.config.demo)
        else:
            normalize_voice(source, part)
        segment.audio_file = part.name
        segment.duration_s = probe_duration(part)
        parts.append(part)

    concat_parts(parts, out_dir / "show.mp3")
    show.audio_file = "show.mp3"
    return show
