"""Turn the listener's wish into Jamendo search filters with one LLM call (D030).

The prompt asks for a small JSON object. It is validated into `WishFilters`; anything
else fails the show with a readable message, like an empty script does (D021).
"""

from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from radio_llama.llm.base import LLMClient, LLMError, LLMResponse, Message
from radio_llama.models import Energy
from radio_llama.music.jamendo import WishFilters
from radio_llama.script.prompts import load_template, render
from radio_llama.script.writer import UI_STRINGS, clean_script
from radio_llama.trace import traced_generate

# Enough for the model's thinking plus a few lines of JSON (D021).
MAX_TOKENS = 2000
TEMPERATURE = 0.2  # a mapping task, not a creative one


def map_wish(
    llm: LLMClient,
    prompts_dir: Path,
    language: Literal["en", "fr"],
    *,
    wish: str,
    style: str,
    energy: Energy | None,
) -> tuple[WishFilters, LLMResponse]:
    """Ask the model for the filters that match `wish`. Returns them with the raw response."""
    strings = UI_STRINGS[language]
    user = render(
        load_template(prompts_dir, language, "wish"),
        {"wish": wish, "style": style, "energy": energy or strings["any_energy"]},
    )
    response = traced_generate(
        llm,
        [Message(role="user", content=user)],
        label="wish",
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )
    text = clean_script(response.text)
    try:
        filters = WishFilters.model_validate_json(text)
    except ValidationError:
        raise LLMError(
            f"The model returned invalid search filters for the wish: {text[:200]!r}"
        ) from None
    return filters, response
