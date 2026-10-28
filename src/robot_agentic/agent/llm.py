"""Calls to Claude: model settings, prompt caching, cost and call log.

Every request of an investigation goes through `ClaudeClient.create`, which records its
tokens, estimated cost and duration in `data/llm_calls.jsonl` (used by the benchmarks).
Authentication follows the Anthropic SDK order: ANTHROPIC_API_KEY, then the `ant auth login`
profile.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import anthropic

log = logging.getLogger(__name__)

# Public prices in $ per million tokens: (input, output, cache read).
# Writing to the cache costs 1.25 times the input price.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 0.20), "claude-haiku-5-5": (0.10, 0.50, 0.01)}


def cost_usd(model: str, usage: Any) -> float:
    """Estimated cost of one call, in dollars, from the `usage` field of an API response."""
    price_in, price_out, price_cache = PRICES.get(model, PRICES["claude-opus-5-5"])
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        usage.input_tokens * price_in + cache_write * price_in * 1.25 + cache_read * price_cache + usage.output_tokens * price_out
    ) / 1e6


class LLM(Protocol):
    """What the graph needs; replaced by a scripted fake LLM in the tests."""

    def create(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        """Send a request and return the response (an object with `content` and `stop_reason`)."""
        ...


@dataclass
class Usage:
    """Running total of a client's calls, to show the cost of an investigation or a benchmark."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0


class ClaudeClient:
    """Claude client configured for the diagnostic agent.

    Settings:
    - adaptive thinking (Claude decides when and how much to think), visible summary;
    - effort `effort` (`high` by default: a diagnosis needs rigour);
    - prompt caching: a breakpoint on the system prompt (shared by every investigation) and
      automatic caching of the conversation, so each turn only pays the full price for the
      new messages;
    - automatic fallback model if the main model declines a request.

    Args:
        model: Claude model id.
        effort: low, medium, high, xhigh or max.
        log_path: JSONL file where one line per call is appended; None to write nothing.
    """

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "high", log_path: Path | None = None) -> None:
        """Create the SDK client; the key is read from the environment at this point."""
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.log_path = log_path
        self.usage = Usage()

    def create(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        """Send one turn of the investigation to Claude and account for its cost.

        Args:
            system: the agent's instructions.
            messages: full history of the investigation, sent again unchanged at each turn.
            tools: tool definitions.

        Returns:
            The SDK response (`content`, `stop_reason`, `usage`).

        Raises:
            anthropic.APIError: if the call fails after the SDK's retries.
        """
        started = time.time()
        log.debug("call to %s: %d messages, %d tools", self.model, len(messages), len(tools))
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            cache_control={"type": "ephemeral"},
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        seconds = time.time() - started

        u = response.usage
        cost = cost_usd(self.model, u)
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        self.usage.calls += 1
        self.usage.input_tokens += u.input_tokens + cache_write
        self.usage.output_tokens += u.output_tokens
        self.usage.cache_read_tokens += cache_read
        self.usage.cost_usd += cost
        if self.log_path:
            entry = {
                "t": time.time(),
                "model": response.model,
                "stop_reason": response.stop_reason,
                "input_tokens": u.input_tokens,
                "cache_write": cache_write,
                "cache_read": cache_read,
                "output_tokens": u.output_tokens,
                "cost_usd": round(cost, 5),
                "seconds": round(seconds, 2),
            }
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as fh:
                fh.write(json.dumps(entry) + "\n")
        log.info(
            "Claude call (%s): %d+%d input tokens (cache %d), %d output, $%.4f, %.1f s, stop: %s",
            response.model, u.input_tokens, cache_write, cache_read, u.output_tokens, cost, seconds, response.stop_reason,
        )  # fmt: skip
        if response.model != self.model:
            log.warning("request handled by the fallback model %s", response.model)
        return response
