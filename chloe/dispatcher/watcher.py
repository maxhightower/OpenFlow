"""OutputWatcher: consumes claude --output-format stream-json stdout."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        from chloe.observer.parser import (
            COST_PER_INPUT_TOKEN,
            COST_PER_OUTPUT_TOKEN,
            DEFAULT_COST_PER_INPUT,
            DEFAULT_COST_PER_OUTPUT,
        )
        # Use a module-level model hint set by the runner
        model = getattr(self, "_model", "claude-sonnet-4-6")
        in_rate = COST_PER_INPUT_TOKEN.get(model, DEFAULT_COST_PER_INPUT)
        out_rate = COST_PER_OUTPUT_TOKEN.get(model, DEFAULT_COST_PER_OUTPUT)
        return round(
            self.input_tokens * in_rate + self.output_tokens * out_rate, 6
        )


@dataclass
class WatcherResult:
    content_chunks: list[str] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    error_message: str | None = None
    raw_lines: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "".join(self.content_chunks)


class OutputWatcher:
    """
    Reads stream-json lines emitted by `claude --output-format stream-json`.

    The format emits one JSON object per line:
      {"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}], ...}}
      {"type": "result", "subtype": "success", "usage": {"input_tokens": N, "output_tokens": M}}

    We collect text chunks and extract the final usage from the result event.
    """

    def __init__(
        self,
        stream: asyncio.StreamReader,
        run_id: str,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self.stream = stream
        self.run_id = run_id
        self.model = model

    async def consume(self) -> WatcherResult:
        result = WatcherResult()

        while True:
            try:
                line_bytes = await asyncio.wait_for(self.stream.readline(), timeout=300.0)
            except asyncio.TimeoutError:
                result.error_message = "Timed out waiting for output"
                break

            if not line_bytes:
                break  # EOF

            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            result.raw_lines.append(line)

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # Plain text line — treat as content
                result.content_chunks.append(line + "\n")
                continue

            event_type = obj.get("type", "")

            if event_type == "assistant":
                # Extract text from message content blocks
                msg = obj.get("message") or {}
                for block in msg.get("content", []):
                    if block.get("type") == "text":
                        result.content_chunks.append(block.get("text", ""))

            elif event_type == "result":
                usage_data = obj.get("usage") or {}
                result.usage = _parse_usage(usage_data, self.model)
                if obj.get("subtype") == "error":
                    result.error_message = obj.get("error", {}).get("message") or "Unknown error"

            elif event_type == "text":
                # Older / simplified format
                result.content_chunks.append(obj.get("text", ""))

            elif event_type == "error":
                result.error_message = obj.get("error", {}).get("message") or str(obj)

        return result


def _parse_usage(data: dict, model: str) -> TokenUsage:
    usage = TokenUsage(
        input_tokens=data.get("input_tokens", 0),
        output_tokens=data.get("output_tokens", 0),
        cache_creation_tokens=data.get("cache_creation_input_tokens", 0),
        cache_read_tokens=data.get("cache_read_input_tokens", 0),
    )
    usage._model = model  # type: ignore[attr-defined]
    return usage
