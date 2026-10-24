"""Appels à Claude : authentification, paramètres du modèle, journal des appels.

Toutes les requêtes passent par `ClaudeClient.create`, qui enregistre pour chacune les
tokens, le coût estimé et la durée dans `data/llm_calls.jsonl` (repris par les benchmarks).
L'authentification suit l'ordre du SDK Anthropic : ANTHROPIC_API_KEY, puis le profil
`ant auth login`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import anthropic

# Prix publics en $ par million de tokens (entrée, sortie, lecture du cache).
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 0.20), "claude-haiku-5-5": (0.10, 0.50, 0.01)}


class LLM(Protocol):
    """Ce dont le graphe a besoin ; remplacé par un faux LLM scripté dans les tests."""

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> Any: ...


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    log: list[dict] = field(default_factory=list)


class ClaudeClient:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "high", log_path: Path | None = None):
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.log_path = log_path
        self.usage = Usage()

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> Any:
        started = time.time()
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            # Le prompt système et la liste d'outils ne changent pas pendant une enquête :
            # ils sont mis en cache, seules les nouvelles données sont facturées plein tarif.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": self.effort},
            # Si le modèle décline une requête, l'API la relance sur un modèle de repli.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        self._record(response, time.time() - started)
        return response

    def _record(self, response: Any, seconds: float) -> None:
        u = response.usage
        price_in, price_out, price_cache = PRICES.get(self.model, PRICES["claude-opus-5-5"])
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        cost = (u.input_tokens * price_in + cache_write * price_in * 1.25 + cache_read * price_cache + u.output_tokens * price_out) / 1e6
        self.usage.calls += 1
        self.usage.input_tokens += u.input_tokens + cache_write
        self.usage.output_tokens += u.output_tokens
        self.usage.cache_read_tokens += cache_read
        self.usage.cost_usd += cost
        self.usage.seconds += seconds
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
        self.usage.log.append(entry)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as fh:
                fh.write(json.dumps(entry) + "\n")
