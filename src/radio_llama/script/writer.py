"""Write the host's script for an intro, a transition or an outro through an LLMClient."""

import re
from pathlib import Path
from typing import Literal

from radio_llama.llm.base import LLMClient, LLMError, LLMResponse, Message
from radio_llama.models import Energy, Fact, Persona, Track
from radio_llama.script.prompts import load_template, render
from radio_llama.script.timing import SPEECH_SECONDS, word_budget

# Language-dependent strings that are not prompts: labels and short sentences the writer
# puts into placeholder values.
UI_STRINGS = {
    "en": {
        "by": "by",
        "no_facts": "(no facts available for this track)",
        "no_notes": "(none)",
        "about": {"artist": "About the artist:", "track": "About the track:"},
        "album": "Album:",
        "tags": "Tags:",
        "energy": "Energy:",
        "energy_line": {
            "calm": "The listener asked for a calm show.",
            "medium": "The listener asked for a medium-energy show.",
            "upbeat": "The listener asked for an upbeat show.",
        },
    },
    "fr": {
        "by": "de",
        "no_facts": "(aucune anecdote disponible pour ce morceau)",
        "no_notes": "(aucune)",
        "about": {"artist": "Sur l'artiste :", "track": "Sur le morceau :"},
        "album": "Album :",
        "tags": "Tags :",
        "energy": "Énergie :",
        "energy_line": {
            "calm": "L'auditeur a demandé une émission calme.",
            "medium": "L'auditeur a demandé une émission d'énergie moyenne.",
            "upbeat": "L'auditeur a demandé une émission entraînante.",
        },
    },
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
    return "\n".join(f"{track.display_title} {by} {track.artist}" for track in tracks)


def format_programme(tracks: list[Track], language: str) -> str:
    """Like `format_track_list`, with "(album, year, tags)" after each track, parts that exist."""
    lines = []
    for line, track in zip(format_track_list(tracks, language).splitlines(), tracks, strict=True):
        parts = [track.album, str(track.year or ""), *track.tags]
        details = ", ".join(part for part in parts if part)
        lines.append(f"{line} ({details})" if details else line)
    return "\n".join(lines)


def format_track_details(track: Track, language: str) -> str:
    """Album and year on one line, tags and energy on another. Only the parts that exist."""
    strings = UI_STRINGS[language]
    first = []
    if track.album:
        first.append(f"{strings['album']} {track.album}")
    if track.year is not None:
        first.append(f"({track.year})")
    second = []
    if track.tags:
        second.append(f"{strings['tags']} {', '.join(track.tags)}")
    if track.energy is not None:
        second.append(f"{strings['energy']} {track.energy}")
    lines = [" ".join(first), ". ".join(second)]
    return "\n".join(line for line in lines if line)


def format_facts(facts: list[Fact], language: str) -> str:
    """A bulleted list of facts, each labelled artist or track, or the "no facts" line."""
    if not facts:
        return UI_STRINGS[language]["no_facts"]
    about = UI_STRINGS[language]["about"]
    return "\n".join(f"- {about[fact.about]} {fact.text}" for fact in facts)


def energy_line(energy: Energy | None, language: str) -> str:
    """The sentence naming the energy the listener asked for, or an empty string."""
    if energy is None:
        return ""
    return UI_STRINGS[language]["energy_line"][energy]


class ScriptWriter:
    def __init__(
        self,
        llm: LLMClient,
        prompts_dir: Path,
        language: Literal["en", "fr"],
        persona: Persona,
        style: str,
        *,
        energy: Energy | None = None,
        # Room for the model's thinking as well as the script (D021).
        max_tokens: int = 2000,
        temperature: float = 0.8,
    ) -> None:
        self._llm = llm
        self._prompts_dir = prompts_dir
        self._language = language
        self._persona = persona
        self._style = style
        self._energy = energy
        self._max_tokens = max_tokens
        self._temperature = temperature
        # Every response, in order, for token accounting (D004).
        self.responses: list[LLMResponse] = []

    def write_intro(self, tracks: list[Track]) -> str:
        return self._write("intro", {"track_list": format_programme(tracks, self._language)})

    def write_transition(self, previous: Track, next_track: Track) -> str:
        return self._write(
            "transition",
            {
                "previous_title": previous.display_title,
                "previous_artist": previous.artist,
                "next_title": next_track.display_title,
                "next_artist": next_track.artist,
                "next_details": format_track_details(next_track, self._language),
                "facts": format_facts(next_track.facts, self._language),
                "host_notes": next_track.host_notes or UI_STRINGS[self._language]["no_notes"],
            },
        )

    def write_outro(self, tracks: list[Track]) -> str:
        return self._write("outro", {"track_list": format_track_list(tracks, self._language)})

    def _write(self, kind: str, values: dict[str, str]) -> str:
        system = render(
            load_template(self._prompts_dir, self._language, "system"),
            {
                "persona_description": self._persona.description,
                "style": self._style,
                "energy_line": energy_line(self._energy, self._language),
            },
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
        script = clean_script(response.text)
        if not script:
            raise LLMError(f"The model returned an empty {kind} script.")
        return script
