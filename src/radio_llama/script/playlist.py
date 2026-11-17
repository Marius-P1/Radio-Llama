"""Ask the LLM for famous songs that fit the show, for the YouTube source (D038).

YouTube's own search by genre returns mixes and compilations, so the song choice is the
model's: one call returns a JSON list of artist and title pairs that the source then
checks on YouTube. Proposing songs is selection, not an on-air fact; nothing the model
says here reaches the host's script. An invalid reply fails the show with a readable
message, like a bad wish mapping does (D030).
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from radio_llama.llm.base import LLMClient, LLMError, LLMResponse, Message
from radio_llama.models import Energy, Track
from radio_llama.music.youtube import SongProposal
from radio_llama.script.prompts import load_template, render
from radio_llama.script.writer import UI_STRINGS, clean_script, format_track_list
from radio_llama.trace import traced_generate

# Enough for the model's thinking plus a short JSON list (D021).
MAX_TOKENS = 2000
TEMPERATURE = 0.2  # a recall task, not a creative one


class Playlist(BaseModel):
    """The JSON object the prompt asks for."""

    songs: list[SongProposal]


def propose_playlist(
    llm: LLMClient,
    prompts_dir: Path,
    language: Literal["en", "fr"],
    *,
    style: str | None,
    energy: Energy | None,
    wish: str | None,
    count: int,
    played: list[Track] | None = None,
) -> tuple[list[SongProposal], LLMResponse]:
    """Ask the model for `count` songs. Returns them with the raw response.

    With no style (D039) the prompt says so and the wish alone sets the theme. `played`
    is what an endless show already played, so the model proposes other songs (D044); a
    show of a set length asks once and passes nothing.
    """
    strings = UI_STRINGS[language]
    user = render(
        load_template(prompts_dir, language, "playlist"),
        {
            "style": style or strings["no_style"],
            "energy": energy or strings["any_energy"],
            "wish": wish or strings["no_wish"],
            "language": strings["language_name"],
            "count": str(count),
            "played": format_track_list(played or [], language) or strings["nothing_yet"],
        },
    )
    response = traced_generate(
        llm,
        [Message(role="user", content=user)],
        label="playlist",
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )
    text = clean_script(response.text)
    try:
        playlist = Playlist.model_validate_json(text)
    except ValidationError:
        raise LLMError(f"The model returned an invalid playlist: {text[:200]!r}") from None
    return playlist.songs, response
