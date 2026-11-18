"""The playlist proposer: one LLM call that returns songs as JSON (D038)."""

from pathlib import Path

import pytest

from radio_llama.llm import FakeLLM, LLMError
from radio_llama.models import Track
from radio_llama.music import SongProposal
from radio_llama.script.playlist import propose_playlist

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PLAYLIST_JSON = (
    '{"songs": [{"artist": "Example Artist", "title": "Blue Window"}, '
    '{"artist": "Lea Example", "title": "Montreal Streets"}]}'
)


def test_propose_playlist_parses_the_songs_and_fills_the_prompt() -> None:
    llm = FakeLLM([PLAYLIST_JSON])

    songs, response = propose_playlist(
        llm,
        PROMPTS_DIR,
        "en",
        style="jazz",
        energy="calm",
        wish="something with a trumpet",
        count=5,
    )

    assert songs == [
        SongProposal(artist="Example Artist", title="Blue Window"),
        SongProposal(artist="Lea Example", title="Montreal Streets"),
    ]
    assert response.text == PLAYLIST_JSON
    (user,) = llm.calls[0]
    assert user.role == "user"
    assert "Style chosen by the listener: jazz" in user.content
    assert "Energy chosen by the listener: calm" in user.content
    assert "The listener's request: something with a trumpet" in user.content
    assert "Language of the show: English" in user.content
    assert "Number of songs: 5" in user.content
    assert "Exactly 5 songs" in user.content
    assert "YouTube" in user.content


def test_propose_playlist_names_any_energy_and_no_wish() -> None:
    llm = FakeLLM([PLAYLIST_JSON])

    propose_playlist(llm, PROMPTS_DIR, "en", style="rock", energy=None, wish=None, count=3)
    propose_playlist(llm, PROMPTS_DIR, "fr", style="rock", energy=None, wish=None, count=3)

    english = llm.calls[0][0].content
    assert "Energy chosen by the listener: any" in english
    assert "The listener's request: (none)" in english
    french = llm.calls[1][0].content
    assert "Énergie choisie par l'auditeur : indifférente" in french
    assert "La demande de l'auditeur : (aucune)" in french
    assert "Langue de l'émission : français" in french
    assert "Nombre de chansons : 3" in french


def test_propose_playlist_without_a_style_makes_the_wish_the_theme() -> None:
    """No style on YouTube (D039): the prompt says so instead of showing a Python None."""
    llm = FakeLLM([PLAYLIST_JSON, PLAYLIST_JSON])
    wish = "90s French rap, upbeat"

    propose_playlist(llm, PROMPTS_DIR, "en", style=None, energy=None, wish=wish, count=5)
    propose_playlist(llm, PROMPTS_DIR, "fr", style=None, energy="calm", wish=wish, count=5)

    english = llm.calls[0][0].content
    assert "Style chosen by the listener: (none: the request is the theme)" in english
    assert "The listener's request: 90s French rap, upbeat" in english
    assert "With no style, the request alone sets the theme." in english
    french = llm.calls[1][0].content
    assert "Style choisi par l'auditeur : (aucun : la demande est le thème)" in french
    assert "Énergie choisie par l'auditeur : calm" in french
    for call in llm.calls:
        assert "None" not in call[0].content


def _played(artist: str, title: str) -> Track:
    return Track(
        id=f"youtube-{title}",
        title=title,
        artist=artist,
        style="",
        album="",
        duration_s=200.0,
        license="YouTube embed",
        license_url="https://www.youtube.com/t/terms",
        track_url="https://www.youtube.com/watch?v=x",
        artist_url="https://www.youtube.com/channel/x",
        video_id="x",
    )


def test_propose_playlist_lists_the_songs_already_played() -> None:
    """An endless show asks again and again: the model is told what to leave out (D044)."""
    llm = FakeLLM([PLAYLIST_JSON])
    played = [_played("Example Artist", "Blue Window"), _played("Lea Example", "Night Train")]

    for language in ("en", "fr"):
        propose_playlist(
            llm, PROMPTS_DIR, language, style=None, energy=None, wish="x", count=8, played=played
        )

    english = llm.calls[0][0].content
    assert "Never propose one of them again:" in english
    assert "Blue Window by Example Artist\nNight Train by Lea Example" in english
    assert "Number of songs: 8" in english
    french = llm.calls[1][0].content
    assert "Ne propose plus jamais l'une d'elles :" in french
    assert "Blue Window de Example Artist\nNight Train de Lea Example" in french


def test_propose_playlist_for_a_show_of_a_set_length_says_nothing_was_played() -> None:
    llm = FakeLLM([PLAYLIST_JSON])

    propose_playlist(llm, PROMPTS_DIR, "en", style="rock", energy=None, wish=None, count=3)
    propose_playlist(llm, PROMPTS_DIR, "fr", style="rock", energy=None, wish=None, count=3)

    assert "Never propose one of them again:\n(nothing yet)" in llm.calls[0][0].content
    assert "Ne propose plus jamais l'une d'elles :\n(rien encore)" in llm.calls[1][0].content


def test_propose_playlist_accepts_a_fenced_reply() -> None:
    llm = FakeLLM([f"```json\n{PLAYLIST_JSON}\n```"])

    songs, _ = propose_playlist(
        llm, PROMPTS_DIR, "en", style="pop", energy=None, wish=None, count=2
    )

    assert len(songs) == 2


@pytest.mark.parametrize(
    "reply",
    [
        "Sure! Here are some songs.",
        '{"songs": [{"artist": "", "title": "x"}]}',
        '{"songs": [{"artist": "A"}]}',
        '[{"artist": "A", "title": "B"}]',
        "",
    ],
)
def test_propose_playlist_rejects_anything_else(reply: str) -> None:
    llm = FakeLLM([reply])

    with pytest.raises(LLMError, match="invalid playlist"):
        propose_playlist(llm, PROMPTS_DIR, "en", style="pop", energy=None, wish=None, count=2)
