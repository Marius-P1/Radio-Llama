from pathlib import Path

import pytest

from radio_llama.llm import FakeLLM
from radio_llama.models import Fact, Track
from radio_llama.personas import get_persona
from radio_llama.script import SPEECH_SECONDS, ScriptWriter, clean_script, word_budget
from radio_llama.script.writer import UI_STRINGS

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
CANNED = "Good evening, this is Radio Llama."


def make_track(number: int, facts: list[Fact] | None = None) -> Track:
    return Track(
        id=f"track-{number}",
        title=f"Blue Window {number}",
        artist=f"The Lanterns {number}",
        style="jazz",
        album="Blue Windows",
        year=2019,
        duration_s=180.0,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        track_url=f"https://example.org/track-{number}",
        artist_url=f"https://example.org/lanterns-{number}",
        audio_file=f"track-{number}.mp3",
        tags=["jazz", "late night"],
        facts=facts or [],
    )


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


def make_writer(language: str = "en") -> tuple[ScriptWriter, FakeLLM]:
    llm = FakeLLM([CANNED])
    persona = get_persona(PROMPTS_DIR, language, "calm")
    writer = ScriptWriter(llm, PROMPTS_DIR, language, persona, "jazz")  # type: ignore[arg-type]
    return writer, llm


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
