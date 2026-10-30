from radio_llama.llm import FakeLLM, LLMResponse, Message


def test_default_response_and_token_counts() -> None:
    llm = FakeLLM()
    messages = [
        Message(role="system", content="You are a radio host."),
        Message(role="user", content="Introduce the show"),
    ]
    response = llm.generate(messages, max_tokens=50, temperature=0.5)
    assert isinstance(response, LLMResponse)
    assert response.text == "This is a fake radio script."
    assert response.input_tokens == 8
    assert response.output_tokens == 6
    assert response.latency_s == 0.0
    assert response.model == "fake"


def test_responses_cycle_when_exhausted() -> None:
    llm = FakeLLM(["one", "two"])
    texts = [llm.generate([], max_tokens=1, temperature=0.0).text for _ in range(5)]
    assert texts == ["one", "two", "one", "two", "one"]


def test_every_call_is_recorded() -> None:
    llm = FakeLLM()
    first = [Message(role="user", content="a")]
    second = [Message(role="user", content="b"), Message(role="assistant", content="c")]
    llm.generate(first, max_tokens=1, temperature=0.0)
    llm.generate(second, max_tokens=1, temperature=0.0)
    assert llm.calls == [first, second]
