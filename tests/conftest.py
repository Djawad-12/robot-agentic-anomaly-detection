"""Shared test helpers: started fake connector, toolbox, fake LLM and silent interface."""

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
    """Fake interface: shows nothing, notes every call in `log`, answers `approve`."""

    approve: bool = True
    log: list[str] = field(default_factory=list)

    def alert(self, alert: Alert) -> None:
        """Note the alert."""
        self.log.append(f"alert {alert.reason}")

    def confirm(self, question: str) -> bool:
        """Note the question and answer `approve`."""
        self.log.append(f"confirm {question}")
        return self.approve

    def ask_pin(self) -> str:
        """Always answer "0000"."""
        return "0000"

    def say(self, text: str) -> None:
        """Note the message."""
        self.log.append(f"say {text}")

    def thinking(self, text: str) -> None:
        """Ignore the agent's reasoning."""

    def tool_call(self, name: str, args: dict[str, Any]) -> None:
        """Note the tool call."""
        self.log.append(f"tool {name}")

    def report(self, report: dict[str, Any] | None, error: str | None, path: Path) -> None:
        """Note the report path."""
        self.log.append(f"report {path}")


class ScriptedLLM:
    """Fake LLM that replays a list of turns.

    Each turn is a list of tool calls (name, arguments); an empty turn only returns text. The
    requests received are kept in `requests`, to check what the agent saw.
    """

    def __init__(self, turns: list[list[tuple[str, dict[str, Any]]]]) -> None:
        """Remember the turns to replay."""
        self.turns = list(turns)
        self.requests: list[dict[str, Any]] = []

    def create(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        """Return the next turn, shaped like an API response (content, stop_reason)."""
        self.requests.append({"messages": list(messages), "tools": tools})
        calls = self.turns.pop(0) if self.turns else []
        content: list[SimpleNamespace] = [SimpleNamespace(type="text", text="Checking.")]
        content += [SimpleNamespace(type="tool_use", id=f"tu_{len(self.requests)}_{i}", name=n, input=a) for i, (n, a) in enumerate(calls)]
        return SimpleNamespace(content=content, stop_reason="tool_use" if calls else "end_turn")


@pytest.fixture
def fake() -> FakeConnector:
    """Brand-new simulated machine, without any fault."""
    return FakeConnector()


@pytest.fixture
def hmi() -> SilentHMI:
    """Fake interface that approves everything."""
    return SilentHMI()


@pytest.fixture
def toolbox(fake: FakeConnector, hmi: SilentHMI, tmp_path: Path) -> Toolbox:
    """Toolbox on the simulated machine, streams started, data in a temporary folder."""
    recorder = Recorder(fake, tmp_path / "recordings").start()
    safety = SafetyPolicy(ask_operator=hmi.confirm)
    tb = Toolbox(fake, recorder, safety, tmp_path / "docs", budget_tokens=20_000)
    time.sleep(0.3)
    return tb
