"""The playlist proposer: one LLM call that returns songs as JSON (D038)."""

from pathlib import Path

import pytest

from radio_llama.llm import FakeLLM, LLMError
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
