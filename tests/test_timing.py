import pytest

from radio_llama.script import word_budget


def test_english_intro_budget() -> None:
    assert word_budget(15, "en") == 38


def test_french_transition_budget() -> None:
    assert word_budget(10, "fr") == 23


def test_unknown_language_raises() -> None:
    with pytest.raises(KeyError):
        word_budget(10, "de")
