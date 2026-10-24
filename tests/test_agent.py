"""Tests du graphe d'enquête, avec la machine simulée et un faux LLM scripté."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from robot_agentic.agent.graph import build_graph
from robot_agentic.agent.modes import run_check
from robot_agentic.agent.tools import Toolbox
from robot_agentic.connectors.fake import FakeConnector

from .conftest import ScriptedLLM, SilentHMI

REPORT: dict[str, Any] = {
    "root_cause": "Frottement anormal sur l'articulation 2",
    "confidence": "élevée",
    "evidence": ["courant +60 % pendant le test"],
    "repair": ["Inspecter le réducteur de l'articulation 2"],
    "references": [],
    "tests_done": [],
}


def test_check_runs_test_and_reports(toolbox: Toolbox, fake: FakeConnector, hmi: SilentHMI, tmp_path: Path) -> None:
    """Contrôle ponctuel : le test est enregistré, l'agent reçoit écarts, graphique et valeurs, puis le rapport est sauvegardé."""
    toolbox.capture_baseline("joint_test", {"joint": 2, "delta_deg": 5})
    fake.inject("friction", joint=2, factor=1.6)
    llm = ScriptedLLM([[("joint_test", {"joint": 2, "delta_deg": 5})], [("submit_report", REPORT)]])
    state = run_check(toolbox, llm, hmi, tmp_path / "reports")

    assert state["report"]["root_cause"] == REPORT["root_cause"]
    saved = json.loads(Path(state["report_path"]).read_text())
    assert saved["recordings"], "le test doit être enregistré"
    result = llm.requests[1]["messages"][-1]["content"][0]["content"]
    assert [b["type"] for b in result] == ["text", "image", "text"]
    assert "joint_2_current_actual" in result[0]["text"]
    assert result[2]["text"].count("\n") > 50


def test_out_of_bounds_test_is_refused_without_moving(toolbox: Toolbox, fake: FakeConnector, hmi: SilentHMI, tmp_path: Path) -> None:
    """Un test hors bornes est refusé et rendu à l'agent comme tel ; la machine ne bouge pas."""
    llm = ScriptedLLM([[("joint_test", {"joint": 1, "delta_deg": 45})], [("submit_report", REPORT)]])
    run_check(toolbox, llm, hmi, tmp_path / "reports")
    result = llm.requests[1]["messages"][-1]["content"][0]["content"]
    assert result.startswith("REFUSÉ")
    assert fake.calls == []


def test_loop_mode_wakes_on_fault_and_asks_operator(toolbox: Toolbox, fake: FakeConnector, hmi: SilentHMI, tmp_path: Path) -> None:
    """Mode boucle : un code d'erreur réveille l'agent, l'opérateur est consulté, le code figure dans le premier message."""
    events = toolbox.recorder.subscribe()
    llm = ScriptedLLM([[("submit_report", REPORT)]])
    graph = build_graph(toolbox, llm, hmi, events=events, reports_dir=tmp_path / "reports", watch_timeout=5)
    fake.inject("fault", joint=2)
    state = graph.invoke({"mode": "loop"})
    assert state["alert"] is not None and "C204A3" in state["alert"].reason
    assert any(line.startswith("confirm Anomalie") for line in hmi.log)
    assert state["report"]["root_cause"]
    assert "C204A3" in llm.requests[0]["messages"][0]["content"]


def test_loop_mode_stops_when_operator_declines(toolbox: Toolbox, fake: FakeConnector, hmi: SilentHMI, tmp_path: Path) -> None:
    """Mode boucle : si l'opérateur refuse l'analyse, aucun appel n'est fait au LLM."""
    hmi.approve = False
    events = toolbox.recorder.subscribe()
    llm = ScriptedLLM([])
    graph = build_graph(toolbox, llm, hmi, events=events, reports_dir=tmp_path / "reports", watch_timeout=5)
    fake.inject("fault")
    state = graph.invoke({"mode": "loop"})
    assert state["approved"] is False and llm.requests == []
