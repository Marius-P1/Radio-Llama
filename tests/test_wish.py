"""The wish mapper: one LLM call that returns Jamendo filters as JSON (D030)."""

from pathlib import Path

import pytest

from radio_llama.llm import FakeLLM, LLMError
from radio_llama.music import WishFilters
from radio_llama.script.wish import map_wish

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
FILTERS_JSON = '{"tags": ["piano", "rainy"], "vocal": "instrumental", "lang": null, "search": null}'


def test_map_wish_parses_the_filters_and_fills_the_prompt() -> None:
    llm = FakeLLM([FILTERS_JSON])

    filters, response = map_wish(
        llm, PROMPTS_DIR, "en", wish="piano for a rainy sunday", style="jazz", energy="calm"
    )

    assert filters == WishFilters(tags=["piano", "rainy"], vocal="instrumental")
    assert response.text == FILTERS_JSON
    (user,) = llm.calls[0]
    assert user.role == "user"
    assert "piano for a rainy sunday" in user.content
    assert "jazz" in user.content
    assert "calm" in user.content


def test_map_wish_names_any_energy_when_none_is_chosen() -> None:
    llm = FakeLLM([FILTERS_JSON])

    map_wish(llm, PROMPTS_DIR, "en", wish="x", style="jazz", energy=None)
    map_wish(llm, PROMPTS_DIR, "fr", wish="x", style="jazz", energy=None)

    assert "Energy chosen by the listener: any" in llm.calls[0][0].content
    assert "Énergie choisie par l'auditeur : indifférente" in llm.calls[1][0].content
    assert "Jamendo" in llm.calls[1][0].content


def test_map_wish_accepts_a_fenced_reply() -> None:
    llm = FakeLLM([f"```json\n{FILTERS_JSON}\n```"])

    filters, _ = map_wish(llm, PROMPTS_DIR, "en", wish="x", style="jazz", energy=None)

    assert filters.tags == ["piano", "rainy"]


def test_map_wish_accepts_an_empty_object() -> None:
    llm = FakeLLM(["{}"])

    filters, _ = map_wish(llm, PROMPTS_DIR, "en", wish="x", style="jazz", energy=None)

    assert filters == WishFilters()


@pytest.mark.parametrize("reply", ["Sure! Here are the filters.", '{"vocal": "humming"}', ""])
def test_map_wish_rejects_anything_else(reply: str) -> None:
    llm = FakeLLM([reply])

    with pytest.raises(LLMError, match="invalid search filters"):
        map_wish(llm, PROMPTS_DIR, "en", wish="x", style="jazz", energy=None)
