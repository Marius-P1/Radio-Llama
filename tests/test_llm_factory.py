from pathlib import Path

import pytest

from radio_llama.config import Settings
from radio_llama.llm import FakeLLM, make_llm_client


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run each test in an empty folder with none of our variables set."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert isinstance(make_llm_client(Settings()), FakeLLM)
