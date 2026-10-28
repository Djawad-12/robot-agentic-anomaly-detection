"""Tests of the investigation graph, with the simulated machine and a scripted fake LLM."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from robot_agentic.agent.graph import build_graph, run_check
from robot_agentic.agent.tools import Toolbox
from robot_agentic.connectors.fake import FakeConnector

from .conftest import ScriptedLLM, SilentHMI

REPORT: dict[str, Any] = {
    "root_cause": "Abnormal friction on joint 2",
    "confidence": "high",
    "evidence": ["current +60 % during the test"],
    "repair": ["Inspect the gearbox of joint 2"],
    "references": [],
    "tests_done": [],
}


def test_check_runs_test_and_reports(toolbox: Toolbox, fake: FakeConnector, hmi: SilentHMI, tmp_path: Path) -> None:
    """Spot check: the test is recorded, the agent gets a summary, a plot and the values, then the report is saved."""
    fake.inject("friction", joint=2, factor=1.6)
    llm = ScriptedLLM([[("joint_test", {"joint": 2, "delta_deg": 5})], [("submit_report", REPORT)]])
    state = run_check(toolbox, llm, hmi, tmp_path / "reports")

    assert state["report"]["root_cause"] == REPORT["root_cause"]
    saved = json.loads(Path(state["report_path"]).read_text())
    assert saved["recordings"], "the test must be recorded"
    result = llm.requests[1]["messages"][-1]["content"][0]["content"]
    assert [b["type"] for b in result] == ["text", "image", "text"]
    assert "joint_2_current_actual" in result[2]["text"]
    assert result[2]["text"].count("\n") > 50


def test_every_movement_needs_approval(toolbox: Toolbox, hmi: SilentHMI, tmp_path: Path) -> None:
    """Two tests in the same investigation mean two questions to the operator."""
    test = ("joint_test", {"joint": 1, "delta_deg": 2})
    llm = ScriptedLLM([[test], [test], [("submit_report", REPORT)]])
    run_check(toolbox, llm, hmi, tmp_path / "reports")
    assert sum(line.startswith("confirm The agent wants to run joint_test") for line in hmi.log) == 2


def test_out_of_bounds_test_is_refused_without_moving(toolbox: Toolbox, fake: FakeConnector, hmi: SilentHMI, tmp_path: Path) -> None:
    """An out-of-bounds test is refused and reported to the agent as such; the machine does not move."""
    llm = ScriptedLLM([[("joint_test", {"joint": 1, "delta_deg": 45})], [("submit_report", REPORT)]])
    run_check(toolbox, llm, hmi, tmp_path / "reports")
    result = llm.requests[1]["messages"][-1]["content"][0]["content"]
    assert result.startswith("REFUSED")
    assert fake.calls == []


def test_loop_mode_wakes_on_fault_and_asks_operator(toolbox: Toolbox, fake: FakeConnector, hmi: SilentHMI, tmp_path: Path) -> None:
    """Loop mode: a fault code wakes the agent up, the operator is asked, the code is in the first message."""
    events = toolbox.recorder.subscribe()
    llm = ScriptedLLM([[("submit_report", REPORT)]])
    graph = build_graph(toolbox, llm, hmi, events=events, reports_dir=tmp_path / "reports", watch_timeout=5)
    fake.inject("fault", joint=2)
    state = graph.invoke({"mode": "loop"})
    assert state["alert"] is not None and "C204A3" in state["alert"].reason
    assert any(line.startswith("confirm Anomaly") for line in hmi.log)
    assert state["report"]["root_cause"]
    assert "C204A3" in llm.requests[0]["messages"][0]["content"]


def test_loop_mode_stops_when_operator_declines(toolbox: Toolbox, fake: FakeConnector, hmi: SilentHMI, tmp_path: Path) -> None:
    """Loop mode: if the operator declines the analysis, the LLM is never called."""
    hmi.approve = False
    events = toolbox.recorder.subscribe()
    llm = ScriptedLLM([])
    graph = build_graph(toolbox, llm, hmi, events=events, reports_dir=tmp_path / "reports", watch_timeout=5)
    fake.inject("fault")
    state = graph.invoke({"mode": "loop"})
    assert state["approved"] is False and llm.requests == []
