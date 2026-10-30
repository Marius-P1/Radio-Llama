import json
from pathlib import Path
from typing import Any

import pytest
from mistralai.client.models import ChatCompletionResponse

from radio_llama.llm import LLMError, Message, MistralClient

FIXTURE = Path(__file__).parent / "fixtures" / "llm" / "mistral_chat_completion.json"

MESSAGES = [
    Message(role="system", content="You are a radio host."),
    Message(role="user", content="Introduce the show."),
]


def load_fixture() -> ChatCompletionResponse:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return ChatCompletionResponse.model_validate(data)


class StubChat:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, Any] = {}

    def complete(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


class StubMistral:
    def __init__(self, chat: StubChat) -> None:
        self.chat = chat


def test_generate_maps_the_recorded_response() -> None:
    chat = StubChat(response=load_fixture())
    client = MistralClient("dummy-key", "mistral-small-latest", client=StubMistral(chat))

    response = client.generate(MESSAGES, max_tokens=64, temperature=0.3)

    assert response.text == "Good evening, this is a recorded test reply."
    assert response.input_tokens == 21
    assert response.output_tokens == 11
    assert response.model == "mistral-small-latest"
    assert response.latency_s >= 0


def test_generate_passes_messages_and_parameters_through() -> None:
    chat = StubChat(response=load_fixture())
    client = MistralClient("dummy-key", "mistral-small-latest", client=StubMistral(chat))

    client.generate(MESSAGES, max_tokens=64, temperature=0.3)

    assert chat.kwargs["model"] == "mistral-small-latest"
    assert chat.kwargs["messages"] == [
        {"role": "system", "content": "You are a radio host."},
        {"role": "user", "content": "Introduce the show."},
    ]
    assert chat.kwargs["max_tokens"] == 64
    assert chat.kwargs["temperature"] == 0.3


def test_generate_joins_text_chunks() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["choices"][0]["message"]["content"] = [
        {"type": "text", "text": "Good evening, "},
        {"type": "text", "text": "this is chunked."},
    ]
    chat = StubChat(response=ChatCompletionResponse.model_validate(data))
    client = MistralClient("dummy-key", "mistral-small-latest", client=StubMistral(chat))

    assert client.generate(MESSAGES, max_tokens=64, temperature=0.3).text == (
        "Good evening, this is chunked."
    )


def test_sdk_errors_become_llm_errors() -> None:
    chat = StubChat(error=RuntimeError("boom"))
    client = MistralClient("dummy-key", "mistral-small-latest", client=StubMistral(chat))

    with pytest.raises(LLMError) as info:
        client.generate(MESSAGES, max_tokens=64, temperature=0.3)

    assert "Mistral" in str(info.value)
    assert "boom" in str(info.value)
    assert isinstance(info.value.__cause__, RuntimeError)
