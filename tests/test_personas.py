from pathlib import Path

import pytest

from radio_llama.personas import UnknownPersonaError, get_persona, load_personas

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
EXPECTED_IDS = ["calm", "energetic", "witty"]


@pytest.mark.parametrize("language", ["en", "fr"])
def test_personas_load_with_every_field_filled(language: str) -> None:
    personas = load_personas(PROMPTS_DIR, language)
    assert [persona.id for persona in personas] == EXPECTED_IDS
    for persona in personas:
        for field, value in persona.model_dump().items():
            assert value, f"{language}/{persona.id}: {field} is empty"


def test_both_languages_declare_the_same_ids_in_the_same_order() -> None:
    en = load_personas(PROMPTS_DIR, "en")
    fr = load_personas(PROMPTS_DIR, "fr")
    assert [persona.id for persona in en] == [persona.id for persona in fr]


def test_get_persona_returns_the_match() -> None:
    assert get_persona(PROMPTS_DIR, "fr", "witty").id == "witty"


def test_get_persona_unknown_id_names_the_id_and_the_known_ones() -> None:
    with pytest.raises(UnknownPersonaError) as info:
        get_persona(PROMPTS_DIR, "en", "grumpy")
    message = str(info.value)
    assert "grumpy" in message
    for persona_id in EXPECTED_IDS:
        assert persona_id in message
