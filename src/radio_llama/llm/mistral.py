"""Mistral adapter on the official `mistralai` SDK."""

import time

# mistralai 3.x is a namespace package: the client lives in `mistralai.client`.
from mistralai.client import Mistral

from radio_llama.llm.base import LLMError, LLMResponse, Message


class MistralClient:
    def __init__(self, api_key: str, model: str, client: Mistral | None = None) -> None:
        self._model = model
        self._client = client if client is not None else Mistral(api_key=api_key)

    def generate(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> LLMResponse:
        started = time.perf_counter()
        try:
            response = self._client.chat.complete(
                model=self._model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:
            raise LLMError(f"Mistral call failed: {exc}") from exc
        latency_s = time.perf_counter() - started

        message = response.choices[0].message
        content = message.content if message is not None else None
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # The SDK may return content as chunks; keep only the text ones.
            text = "".join(
                chunk.text for chunk in content if getattr(chunk, "type", None) == "text"
            )
        else:
            text = ""

        return LLMResponse(
            text=text,
            input_tokens=response.usage.prompt_tokens or 0,
            output_tokens=response.usage.completion_tokens or 0,
            latency_s=latency_s,
            model=response.model,
        )
