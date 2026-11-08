from pathlib import Path

import pytest

from radio_llama.llm import FakeLLM, LLMError
from radio_llama.models import Fact, Track
from radio_llama.personas import get_persona
from radio_llama.script import SPEECH_SECONDS, ScriptWriter, clean_script, word_budget
from radio_llama.script.writer import (
    UI_STRINGS,
    energy_line,
    format_facts,
    format_programme,
    format_track_details,
    format_track_list,
)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
CANNED = "Good evening, this is Radio Llama."


def make_track(number: int, facts: list[Fact] | None = None, **overrides) -> Track:
    fields = {
        "id": f"track-{number}",
        "title": f"Blue Window {number}",
        "artist": f"The Lanterns {number}",
        "style": "jazz",
        "album": "Blue Windows",
        "year": 2019,
        "duration_s": 180.0,
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "track_url": f"https://example.org/track-{number}",
        "artist_url": f"https://example.org/lanterns-{number}",
        "audio_file": f"track-{number}.mp3",
        "tags": ["jazz", "late night"],
        "facts": facts or [],
    }
    return Track(**{**fields, **overrides})


TRACKS = [
    make_track(
        1, [Fact(text="Recorded in one take in a kitchen.", source_url="https://a.example")]
    ),
    make_track(
        2,
        [
            Fact(text="The trumpet solo was improvised.", source_url="https://b.example"),
            Fact(text="Released under a Creative Commons licence.", source_url="https://c.example"),
        ],
    ),
    make_track(3),
]
# A track whose title is not speakable as is, with a note for the host and typed facts.
SPOKEN = make_track(
    4,
    [
        Fact(text="Born in Oslo.", about="artist", source_url="https://d.example"),
        Fact(text="Mixed on tape.", about="track", source_url="https://e.example"),
    ],
    title="Blue Window 4 (ID 1296)",
    spoken_title="Blue Window Four",
    host_notes="Do not name the label.",
    energy="calm",
    notes="Curator only.",
)


def make_writer(language: str = "en") -> tuple[ScriptWriter, FakeLLM]:
    llm = FakeLLM([CANNED])
    persona = get_persona(PROMPTS_DIR, language, "calm")
    writer = ScriptWriter(llm, PROMPTS_DIR, language, persona, "jazz")  # type: ignore[arg-type]
    return writer, llm


def test_an_empty_script_is_an_error() -> None:
    """A blank reply must fail the show, not become half a second of silence on air."""
    llm = FakeLLM(['""'])
    persona = get_persona(PROMPTS_DIR, "en", "calm")
    writer = ScriptWriter(llm, PROMPTS_DIR, "en", persona, "jazz")

    with pytest.raises(LLMError, match="empty transition script"):
        writer.write_transition(TRACKS[0], TRACKS[1])


def test_intro_prompt_carries_tracks_budget_persona_and_style() -> None:
    writer, llm = make_writer()
    text = writer.write_intro(TRACKS)

    assert text == CANNED
    assert len(writer.responses) == 1
    system, user = llm.calls[0]
    assert system.role == "system"
    assert user.role == "user"
    assert get_persona(PROMPTS_DIR, "en", "calm").description in system.content
    assert "jazz" in system.content
    for track in TRACKS:
        assert track.title in user.content
        assert track.artist in user.content
    assert str(word_budget(SPEECH_SECONDS["intro"], "en")) in user.content


def test_transition_prompt_carries_both_tracks_and_next_facts() -> None:
    writer, llm = make_writer()
    writer.write_transition(TRACKS[0], TRACKS[1])

    _, user = llm.calls[0]
    assert TRACKS[0].title in user.content
    assert TRACKS[0].artist in user.content
    assert TRACKS[1].title in user.content
    assert TRACKS[1].artist in user.content
    for fact in TRACKS[1].facts:
        assert fact.text in user.content
    assert TRACKS[0].facts[0].text not in user.content
    assert str(word_budget(SPEECH_SECONDS["transition"], "en")) in user.content


def test_transition_prompt_carries_details_labelled_facts_and_host_notes() -> None:
    writer, llm = make_writer()
    writer.write_transition(TRACKS[0], SPOKEN)

    _, user = llm.calls[0]
    assert "Blue Window Four by The Lanterns 4" in user.content
    assert "(ID 1296)" not in user.content
    assert "Album: Blue Windows (2019)" in user.content
    assert "Tags: jazz, late night. Energy: calm" in user.content
    assert "- About the artist: Born in Oslo." in user.content
    assert "- About the track: Mixed on tape." in user.content
    assert "Do not name the label." in user.content
    assert "Curator only" not in user.content


def test_intro_and_outro_use_the_spoken_title() -> None:
    writer, llm = make_writer()
    writer.write_intro([SPOKEN])
    writer.write_outro([SPOKEN])

    for _, user in llm.calls:
        assert "Blue Window Four by The Lanterns 4" in user.content
        assert "(ID 1296)" not in user.content
    assert "(Blue Windows, 2019, jazz, late night)" in llm.calls[0][1].content
    assert "Blue Windows" not in llm.calls[1][1].content


def test_system_prompt_names_the_energy_only_when_asked() -> None:
    llm = FakeLLM([CANNED])
    persona = get_persona(PROMPTS_DIR, "en", "calm")
    ScriptWriter(llm, PROMPTS_DIR, "en", persona, "jazz", energy="upbeat").write_intro(TRACKS)
    ScriptWriter(llm, PROMPTS_DIR, "en", persona, "jazz").write_intro(TRACKS)

    assert "The listener asked for an upbeat show." in llm.calls[0][0].content
    assert "listener asked" not in llm.calls[1][0].content


def test_system_prompt_carries_the_clock_when_given() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    llm = FakeLLM([CANNED])
    persona = get_persona(PROMPTS_DIR, "en", "calm")
    now = datetime(2026, 11, 8, 16, 45, tzinfo=ZoneInfo("Europe/Paris"))
    ScriptWriter(llm, PROMPTS_DIR, "en", persona, "jazz", now=now).write_intro(TRACKS)
    ScriptWriter(llm, PROMPTS_DIR, "en", persona, "jazz").write_intro(TRACKS)

    assert "Sunday 8 November 2026, 16:45, in the afternoon" in llm.calls[0][0].content
    assert "Local time" not in llm.calls[1][0].content


def test_format_helpers_leave_out_missing_parts() -> None:
    bare = make_track(5, album="", year=None, tags=[])

    assert format_track_list([bare], "en") == "Blue Window 5 by The Lanterns 5"
    assert format_programme([bare], "en") == "Blue Window 5 by The Lanterns 5"
    assert format_track_details(bare, "en") == ""
    assert format_track_details(make_track(6, tags=[]), "fr") == "Album : Blue Windows (2019)"
    assert format_facts([], "fr") == UI_STRINGS["fr"]["no_facts"]
    assert energy_line(None, "fr") == ""
    assert energy_line("calm", "fr") == "L'auditeur a demandé une émission calme."


def test_transition_without_facts_says_so() -> None:
    writer, llm = make_writer()
    writer.write_transition(TRACKS[1], TRACKS[2])

    _, user = llm.calls[0]
    assert UI_STRINGS["en"]["no_facts"] in user.content


def test_outro_prompt_carries_tracks_budget_persona_and_style() -> None:
    writer, llm = make_writer()
    text = writer.write_outro(TRACKS)

    assert text == CANNED
    assert len(writer.responses) == 1
    system, user = llm.calls[0]
    assert get_persona(PROMPTS_DIR, "en", "calm").description in system.content
    assert "jazz" in system.content
    for track in TRACKS:
        assert track.title in user.content
        assert track.artist in user.content
    assert str(word_budget(SPEECH_SECONDS["outro"], "en")) in user.content


def test_responses_accumulate_across_calls() -> None:
    writer, _ = make_writer()
    writer.write_intro(TRACKS)
    writer.write_transition(TRACKS[0], TRACKS[1])
    writer.write_outro(TRACKS)
    assert len(writer.responses) == 3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("```text\nHello listener.\n```", "Hello listener."),
        ("```\nHello listener.\n```", "Hello listener."),
        ('"Hello listener."', "Hello listener."),
        ("\u201cHello listener.\u201d", "Hello listener."),
        ("First line.\n\n\n\nSecond line.", "First line.\n\nSecond line."),
        ("  Hello listener.  \n", "Hello listener."),
        ("Hello listener.", "Hello listener."),
    ],
)
def test_clean_script(raw: str, expected: str) -> None:
    assert clean_script(raw) == expected


def test_clean_script_keeps_inner_quotes() -> None:
    assert clean_script('She said "hello" twice.') == 'She said "hello" twice.'


def test_french_writer_runs_all_three_kinds() -> None:
    writer, llm = make_writer("fr")
    writer.write_intro(TRACKS)
    writer.write_transition(TRACKS[0], TRACKS[1])
    writer.write_outro(TRACKS)

    assert len(writer.responses) == 3
    for system, _ in llm.calls:
        assert "Parle en français" in system.content
    intro_user = llm.calls[0][1]
    assert f"{TRACKS[0].title} de {TRACKS[0].artist}" in intro_user.content
    transition_user = llm.calls[1][1]
    assert f"{TRACKS[0].title} de {TRACKS[0].artist}" in transition_user.content
    assert f"{TRACKS[1].title} de {TRACKS[1].artist}" in transition_user.content
