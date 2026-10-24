"""Appels à Claude : authentification, paramètres du modèle, coût et journal des appels.

Toutes les requêtes passent par `ClaudeClient.create`, qui enregistre pour chacune les
tokens, le coût estimé et la durée dans `data/llm_calls.jsonl` (repris par les benchmarks).
L'authentification suit l'ordre du SDK Anthropic : ANTHROPIC_API_KEY, puis le profil
`ant auth login`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import anthropic

log = logging.getLogger(__name__)

# Prix publics en $ par million de tokens : (entrée, sortie, lecture du cache).
# L'écriture dans le cache coûte 1,25 fois le prix d'entrée.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 0.20), "claude-haiku-5-5": (0.10, 0.50, 0.01)}


class LLM(Protocol):
    """Ce dont le graphe a besoin ; remplacé par un faux LLM scripté dans les tests."""

    def create(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        """Envoie une requête et renvoie la réponse (objet avec `content` et `stop_reason`)."""
        ...


@dataclass
class Usage:
    """Cumul des appels d'un client, pour afficher le coût d'une enquête ou d'un benchmark."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    log: list[dict[str, Any]] = field(default_factory=list)


class ClaudeClient:
    """Client Claude configuré pour l'agent de diagnostic.

    Réglages :
    - réflexion adaptative (Claude décide quand et combien réfléchir), résumé visible ;
    - effort `effort` (`high` par défaut : un diagnostic demande de la rigueur) ;
    - prompt système et outils mis en cache, car identiques à chaque tour d'une enquête ;
    - modèle de repli automatique si une requête est déclinée par le modèle principal.

    Args:
        model: identifiant du modèle Claude.
        effort: low, medium, high, xhigh ou max.
        log_path: fichier JSONL où ajouter une ligne par appel ; None pour ne rien écrire.
    """

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "high", log_path: Path | None = None) -> None:
        """Crée le client du SDK ; la clé est lue dans l'environnement à ce moment-là."""
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.log_path = log_path
        self.usage = Usage()

    def create(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        """Envoie un tour de l'enquête à Claude et comptabilise son coût.

        Args:
            system: consignes de l'agent.
            messages: historique complet de l'enquête, renvoyé à l'identique à chaque tour.
            tools: définitions des outils.

        Returns:
            La réponse du SDK (`content`, `stop_reason`, `usage`).

        Raises:
            anthropic.APIError: en cas d'échec de l'appel, après les nouvelles tentatives du SDK.
        """
        started = time.time()
        log.debug("appel à %s : %d messages, %d outils", self.model, len(messages), len(tools))
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        seconds = time.time() - started

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
        log.info(
            "appel Claude (%s) : %d+%d tokens en entrée (cache %d), %d en sortie, %.4f $, %.1f s, fin : %s",
            response.model, u.input_tokens, cache_write, cache_read, u.output_tokens, cost, seconds, response.stop_reason,
        )  # fmt: skip
        if response.model != self.model:
            log.warning("requête traitée par le modèle de repli %s", response.model)
        return response
