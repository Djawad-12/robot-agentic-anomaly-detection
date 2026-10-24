"""Outils communs aux tests : faux connecteur démarré, boîte à outils, faux LLM scripté."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from robot_agentic.agent.tools import Toolbox
from robot_agentic.connectors.fake import FakeConnector
from robot_agentic.core.recorder import Recorder
from robot_agentic.core.safety import SafetyPolicy


@dataclass
class SilentHMI:
    approve: bool = True
    log: list[str] = field(default_factory=list)

    def alert(self, alert): self.log.append(f"alert {alert.reason}")
    def confirm(self, question): self.log.append(f"confirm {question}"); return self.approve
    def ask_pin(self): return "0000"
    def say(self, text): self.log.append(f"say {text}")
    def thinking(self, text): pass
    def tool_call(self, name, args): self.log.append(f"tool {name}")
    def report(self, report, error, path): self.log.append(f"report {path}")


class ScriptedLLM:
    """Faux LLM : rejoue une liste de tours. Chaque tour est une liste d'appels d'outils
    (nom, arguments) ; un tour vide renvoie seulement du texte."""

    def __init__(self, turns: list[list[tuple[str, dict]]]):
        self.turns = list(turns)
        self.requests: list[dict] = []

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> Any:
        self.requests.append({"messages": list(messages), "tools": tools})
        calls = self.turns.pop(0) if self.turns else []
        content = [SimpleNamespace(type="text", text="Je vérifie.")]
        content += [SimpleNamespace(type="tool_use", id=f"tu_{len(self.requests)}_{i}", name=n, input=a) for i, (n, a) in enumerate(calls)]
        return SimpleNamespace(content=content, stop_reason="tool_use" if calls else "end_turn")


@pytest.fixture
def fake():
    return FakeConnector()


@pytest.fixture
def toolbox(fake, tmp_path):
    hmi = SilentHMI()
    recorder = Recorder(fake, tmp_path / "recordings").start()
    safety = SafetyPolicy(ask_operator=hmi.confirm)
    tb = Toolbox(fake, recorder, safety, tmp_path / "baselines", tmp_path / "docs", budget_tokens=20_000)
    tb.hmi = hmi
    time.sleep(0.3)
    return tb
