"""Anthropic adapter on the official `anthropic` SDK."""

import time

# Imported under an alias: this module is itself named `anthropic`.
import anthropic as anthropic_sdk

from radio_llama.llm.base import LLMError, LLMResponse, Message


class AnthropicClient:
    def __init__(
        self, api_key: str, model: str, client: anthropic_sdk.Anthropic | None = None
    ) -> None:
        self._model = model
        self._client = client if client is not None else anthropic_sdk.Anthropic(api_key=api_key)

    def generate(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> LLMResponse:
        # The SDK takes the system prompt as its own argument, not as a message.
        system_parts = [m.content for m in messages if m.role == "system"]
        kwargs: dict[str, object] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": m.role, "content": m.content} for m in messages if m.role != "system"
            ],
            # Claude 5 models think before answering and the thinking counts against
            # `max_tokens`. A radio script needs little thought; at the default effort the
            # model has used the whole budget thinking and returned no text (D021).
            "output_config": {"effort": "low"},
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        # The Anthropic API stopped accepting `temperature` (API version 2026-10-21, SDK 2.3.0
        # has no parameter). It is accepted here so every client has the same signature,
        # and ignored. Mistral honours it.

        started = time.perf_counter()
        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"Anthropic call failed: {exc}") from exc
        latency_s = time.perf_counter() - started

        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise LLMError(
                f"Anthropic returned no text (stop reason: {response.stop_reason}, "
                f"{response.usage.output_tokens} output tokens)."
            )
        return LLMResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_s=latency_s,
            model=response.model,
        )
