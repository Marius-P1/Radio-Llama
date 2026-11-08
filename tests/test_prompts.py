from pathlib import Path
from string import Template

import pytest

from radio_llama.script import PROMPT_NAMES, load_template, placeholders, render

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

EXPECTED_PLACEHOLDERS = {
    "system": {"persona_description", "style", "time_line", "energy_line", "wish_line"},
    "intro": {"track_list", "word_budget"},
    "transition": {
        "instructions",
        "previous_title",
        "previous_artist",
        "next_title",
        "next_artist",
        "next_details",
        "facts",
        "host_notes",
        "transcript",
        "word_budget",
    },
    "outro": {"track_list", "transcript", "word_budget"},
    "wish": {"wish", "style", "energy"},
}


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_both_languages_declare_the_same_placeholders(name: str) -> None:
    en = placeholders(load_template(PROMPTS_DIR, "en", name))
    fr = placeholders(load_template(PROMPTS_DIR, "fr", name))
    assert en == fr


@pytest.mark.parametrize("language", ["en", "fr"])
@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_placeholders_are_exactly_the_documented_set(language: str, name: str) -> None:
    template = load_template(PROMPTS_DIR, language, name)
    assert placeholders(template) == EXPECTED_PLACEHOLDERS[name]


@pytest.mark.parametrize("language", ["en", "fr"])
@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_prompt_files_stay_short(language: str, name: str) -> None:
    path = PROMPTS_DIR / language / f"{name}.md"
    line_count = len(path.read_text(encoding="utf-8").splitlines())
    assert line_count < 25


def test_render_raises_on_missing_value() -> None:
    with pytest.raises(KeyError):
        render(Template("Hello $name, about $word_budget words."), {"name": "listener"})
