"""Write the host's script for an intro, a transition or an outro through an LLMClient."""

import re
from pathlib import Path
from typing import Literal

from radio_llama.llm.base import LLMClient, LLMResponse, Message
from radio_llama.models import Fact, Persona, Track
from radio_llama.script.prompts import load_template, render
from radio_llama.script.timing import SPEECH_SECONDS, word_budget

# Language-dependent strings that are not prompts.
UI_STRINGS = {
    "en": {"by": "by", "no_facts": "(no facts available for this track)"},
    "fr": {"by": "de", "no_facts": "(aucune anecdote disponible pour ce morceau)"},
}

# A whole text wrapped in ``` fences, with or without a language tag after the opening fence.
_FENCE = re.compile(r"\A```[^\n]*\n(.*?)\n?```\Z", re.DOTALL)
# Straight and curly double quotes, as (opening, closing) pairs.
_QUOTE_PAIRS = {('"', '"'), ("\u201c", "\u201d")}
_BLANK_LINES = re.compile(r"\n\s*\n")


def clean_script(text: str) -> str:
    """Strip the wrapping a model may add around the spoken text. Nothing else."""
    text = text.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    if len(text) >= 2 and (text[0], text[-1]) in _QUOTE_PAIRS:
        text = text[1:-1].strip()
    return _BLANK_LINES.sub("\n\n", text)


def format_track_list(tracks: list[Track], language: str) -> str:
    """One line per track: "Title by Artist" in English, "Title de Artist" in French."""
    by = UI_STRINGS[language]["by"]
    return "\n".join(f"{track.title} {by} {track.artist}" for track in tracks)


def format_facts(facts: list[Fact], language: str) -> str:
    """A bulleted list of fact texts, or the language's "no facts" line."""
    if not facts:
        return UI_STRINGS[language]["no_facts"]
    return "\n".join(f"- {fact.text}" for fact in facts)


class ScriptWriter:
    def __init__(
        self,
        llm: LLMClient,
        prompts_dir: Path,
        language: Literal["en", "fr"],
        persona: Persona,
        style: str,
        *,
        max_tokens: int = 400,
        temperature: float = 0.8,
    ) -> None:
        self._llm = llm
        self._prompts_dir = prompts_dir
        self._language = language
        self._persona = persona
        self._style = style
        self._max_tokens = max_tokens
        self._temperature = temperature
        # Every response, in order, for token accounting (D004).
        self.responses: list[LLMResponse] = []

    def write_intro(self, tracks: list[Track]) -> str:
        return self._write("intro", {"track_list": format_track_list(tracks, self._language)})

    def write_transition(self, previous: Track, next_track: Track) -> str:
        return self._write(
            "transition",
            {
                "previous_title": previous.title,
                "previous_artist": previous.artist,
                "next_title": next_track.title,
                "next_artist": next_track.artist,
                "facts": format_facts(next_track.facts, self._language),
            },
        )

    def write_outro(self, tracks: list[Track]) -> str:
        return self._write("outro", {"track_list": format_track_list(tracks, self._language)})

    def _write(self, kind: str, values: dict[str, str]) -> str:
        system = render(
            load_template(self._prompts_dir, self._language, "system"),
            {"persona_description": self._persona.description, "style": self._style},
        )
        budget = word_budget(SPEECH_SECONDS[kind], self._language)
        user = render(
            load_template(self._prompts_dir, self._language, kind),
            {**values, "word_budget": str(budget)},
        )
        response = self._llm.generate(
            [Message(role="system", content=system), Message(role="user", content=user)],
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        self.responses.append(response)
        return clean_script(response.text)
