"""Outils communs aux tests : faux connecteur démarré, boîte à outils, faux LLM et fausse IHM."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from robot_agentic.agent.tools import Toolbox
from robot_agentic.connectors.fake import FakeConnector
from robot_agentic.core.events import Alert
from robot_agentic.core.recorder import Recorder
from robot_agentic.core.safety import SafetyPolicy


@dataclass
class SilentHMI:
    """Fausse interface : n'affiche rien, note chaque appel dans `log`, répond `approve`."""

    approve: bool = True
    log: list[str] = field(default_factory=list)

    def alert(self, alert: Alert) -> None:
        """Note l'alerte."""
        self.log.append(f"alert {alert.reason}")

    def confirm(self, question: str) -> bool:
        """Note la question et répond `approve`."""
        self.log.append(f"confirm {question}")
        return self.approve

    def ask_pin(self) -> str:
        """Répond toujours « 0000 »."""
        return "0000"

    def say(self, text: str) -> None:
        """Note le message."""
        self.log.append(f"say {text}")

    def thinking(self, text: str) -> None:
        """Ignore la réflexion de l'agent."""

    def tool_call(self, name: str, args: dict[str, Any]) -> None:
        """Note l'appel d'outil."""
        self.log.append(f"tool {name}")

    def report(self, report: dict[str, Any] | None, error: str | None, path: Path) -> None:
        """Note le chemin du rapport."""
        self.log.append(f"report {path}")


class ScriptedLLM:
    """Faux LLM qui rejoue une liste de tours.

    Chaque tour est une liste d'appels d'outils (nom, arguments) ; un tour vide ne renvoie que
    du texte. Les requêtes reçues sont gardées dans `requests` pour vérifier ce que l'agent a vu.
    """

    def __init__(self, turns: list[list[tuple[str, dict[str, Any]]]]) -> None:
        """Mémorise les tours à rejouer."""
        self.turns = list(turns)
        self.requests: list[dict[str, Any]] = []

    def create(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        """Renvoie le tour suivant, sous la forme d'une réponse de l'API (content, stop_reason)."""
        self.requests.append({"messages": list(messages), "tools": tools})
        calls = self.turns.pop(0) if self.turns else []
        content: list[SimpleNamespace] = [SimpleNamespace(type="text", text="Je vérifie.")]
        content += [SimpleNamespace(type="tool_use", id=f"tu_{len(self.requests)}_{i}", name=n, input=a) for i, (n, a) in enumerate(calls)]
        return SimpleNamespace(content=content, stop_reason="tool_use" if calls else "end_turn")


@pytest.fixture
def fake() -> FakeConnector:
    """Machine simulée neuve, sans panne."""
    return FakeConnector()


@pytest.fixture
def hmi() -> SilentHMI:
    """Fausse interface qui accepte tout."""
    return SilentHMI()


@pytest.fixture
def toolbox(fake: FakeConnector, hmi: SilentHMI, tmp_path: Path) -> Toolbox:
    """Boîte à outils sur la machine simulée, flux démarrés, données dans un dossier temporaire."""
    recorder = Recorder(fake, tmp_path / "recordings").start()
    safety = SafetyPolicy(ask_operator=hmi.confirm)
    tb = Toolbox(fake, recorder, safety, tmp_path / "baselines", tmp_path / "docs", budget_tokens=20_000)
    time.sleep(0.3)
    return tb
