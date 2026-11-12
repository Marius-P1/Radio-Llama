"""A per-show record of every external API call (D037).

After a show is generated this is the only way to see what the host was told. A trace is a
folder of JSON Lines files, one per remote service: `llm.jsonl`, `tts.jsonl`,
`jamendo.jsonl`, `musicbrainz.jsonl`, `wikidata.jsonl`, `wikipedia.jsonl`, plus
`facts.jsonl` for facts answers served from a cache. One JSON object per line per call,
with the UTC timestamp, the latency, the error text when the call failed, what was sent
and what came back in full. Audio is never recorded, only its path and byte count.

The current trace folder lives in a context variable. The pipeline sets it for one run;
a record made with no current trace is dropped, so a client called on its own stays
silent. Writes are synchronous: appending one record of a few kilobytes costs about half
a millisecond on a laptop (measured for the MAR-15 PR), against calls that take seconds.

Secrets never reach a trace: no API keys, no request headers, and the Jamendo client id is
stripped by its caller.
"""

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

import httpx

from radio_llama.llm.base import LLMClient, LLMError, LLMResponse, Message

TRACE_DIR = "trace"  # under a show folder
SCRIPTS_DIR = "scripts"  # under the output folder, for `radio-llama script`

_current: ContextVar[Path | None] = ContextVar("trace_folder", default=None)


@contextmanager
def tracing(folder: Path) -> Iterator[Path]:
    """Make `folder` the current trace for the block. Files appear on the first record."""
    token = _current.set(folder)
    try:
        yield folder
    finally:
        _current.reset(token)


def current() -> Path | None:
    """The current trace folder, or None outside a traced run."""
    return _current.get()


def script_trace_dir(output_dir: Path, show_id: str) -> Path:
    """Where `radio-llama script` writes its trace: it has no show folder."""
    return output_dir / SCRIPTS_DIR / show_id


def record(service: str, started: float, *, error: str | None = None, **fields: object) -> None:
    """Append one record to `<trace>/<service>.jsonl`. Nothing when no trace is current.

    `started` is the `time.perf_counter()` taken before the call; the latency is measured
    here so every service records it the same way.
    """
    folder = _current.get()
    if folder is None:
        return
    line = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "latency_s": round(time.perf_counter() - started, 4),
        "error": error,
        **fields,
    }
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{service}.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(line, ensure_ascii=False) + "\n")


def response_body(response: httpx.Response) -> object:
    """The JSON of a reply, or its text when it is not JSON, for the record."""
    try:
        return response.json()
    except ValueError:
        return response.text


def traced_generate(
    llm: LLMClient,
    messages: list[Message],
    *,
    label: str,
    max_tokens: int,
    temperature: float,
) -> LLMResponse:
    """`llm.generate` with the call recorded in `llm.jsonl`, answer or error.

    `label` says what the call was for, for example "wish" or "intro".
    """
    sent = {
        "label": label,
        "provider": getattr(llm, "provider", type(llm).__name__),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [message.model_dump() for message in messages],
    }
    started = time.perf_counter()
    try:
        response = llm.generate(messages, max_tokens=max_tokens, temperature=temperature)
    except LLMError as exc:
        record(
            "llm",
            started,
            error=str(exc),
            **sent,
            model=None,
            text=None,
            input_tokens=None,
            output_tokens=None,
        )
        raise
    record(
        "llm",
        started,
        **sent,
        model=response.model,
        text=response.text,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
    return response
