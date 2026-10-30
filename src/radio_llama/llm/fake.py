"""A deterministic LLM for tests. No network, no randomness."""

from radio_llama.llm.base import LLMResponse, Message


class FakeLLM:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = responses or ["This is a fake radio script."]
        self._next = 0
        self.calls: list[list[Message]] = []

    def generate(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> LLMResponse:
        self.calls.append(list(messages))
        text = self._responses[self._next % len(self._responses)]
        self._next += 1
        return LLMResponse(
            text=text,
            input_tokens=sum(len(m.content.split()) for m in messages),
            output_tokens=len(text.split()),
            latency_s=0.0,
            model="fake",
        )
