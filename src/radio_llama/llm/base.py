"""The LLM interface every adapter implements (D004, D014)."""

from typing import Literal, Protocol

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str


class LLMResponse(BaseModel):
    text: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    model: str


class LLMError(Exception):
    """A provider call failed. The message is readable and names the provider."""


class LLMClient(Protocol):
    def generate(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> LLMResponse: ...
