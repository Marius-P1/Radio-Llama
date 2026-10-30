import json
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import Message as SDKMessage

from radio_llama.llm import AnthropicClient, LLMError, Message

FIXTURE = Path(__file__).parent / "fixtures" / "llm" / "anthropic_message.json"

MESSAGES = [
    Message(role="system", content="You are a radio host."),
    Message(role="user", content="Introduce the show."),
]


def load_fixture() -> SDKMessage:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return SDKMessage.model_validate(data)


class StubMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


class StubAnthropic:
    def __init__(self, messages: StubMessages) -> None:
        self.messages = messages


def make_client(stub: StubMessages) -> AnthropicClient:
    return AnthropicClient("dummy-key", "claude-fake", client=StubAnthropic(stub))


def test_generate_maps_the_recorded_response() -> None:
    stub = StubMessages(response=load_fixture())

    response = make_client(stub).generate(MESSAGES, max_tokens=64, temperature=0.3)

    assert response.text == "Good evening, this is a recorded test reply."
    assert response.input_tokens == 21
    assert response.output_tokens == 11
    assert response.model == "claude-fake"
    assert response.latency_s >= 0


def test_generate_passes_model_and_max_tokens_through_but_not_temperature() -> None:
    stub = StubMessages(response=load_fixture())

    make_client(stub).generate(MESSAGES, max_tokens=64, temperature=0.3)

    assert stub.kwargs["model"] == "claude-fake"
    assert stub.kwargs["max_tokens"] == 64
    # The Anthropic API no longer accepts temperature; the adapter must not forward it.
    assert "temperature" not in stub.kwargs


def test_system_messages_are_joined_into_the_system_argument() -> None:
    stub = StubMessages(response=load_fixture())
    messages = [
        Message(role="system", content="You are a radio host."),
        Message(role="system", content="Speak in English."),
        Message(role="user", content="Introduce the show."),
    ]

    make_client(stub).generate(messages, max_tokens=64, temperature=0.3)

    assert stub.kwargs["system"] == "You are a radio host.\n\nSpeak in English."
    assert stub.kwargs["messages"] == [{"role": "user", "content": "Introduce the show."}]


def test_system_argument_is_absent_without_system_messages() -> None:
    stub = StubMessages(response=load_fixture())

    make_client(stub).generate(
        [Message(role="user", content="Introduce the show.")], max_tokens=64, temperature=0.3
    )

    assert "system" not in stub.kwargs


def test_only_text_blocks_make_up_the_text() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["content"] = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "Good evening, "},
        {"type": "text", "text": "two blocks."},
    ]
    stub = StubMessages(response=SDKMessage.model_validate(data))

    response = make_client(stub).generate(MESSAGES, max_tokens=64, temperature=0.3)

    assert response.text == "Good evening, two blocks."


def test_sdk_errors_become_llm_errors() -> None:
    stub = StubMessages(error=RuntimeError("boom"))

    with pytest.raises(LLMError) as info:
        make_client(stub).generate(MESSAGES, max_tokens=64, temperature=0.3)

    assert "Anthropic" in str(info.value)
    assert "boom" in str(info.value)
    assert isinstance(info.value.__cause__, RuntimeError)
