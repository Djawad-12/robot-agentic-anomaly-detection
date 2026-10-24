from __future__ import annotations

import json

from conftest import ScriptedLLM

from robot_agentic.agent.graph import build_graph
from robot_agentic.agent.modes import run_check

REPORT = {
    "root_cause": "Frottement anormal sur l'articulation 2",
    "confidence": "élevée",
    "evidence": ["courant +60 % pendant le test"],
    "repair": ["Inspecter le réducteur de l'articulation 2"],
    "references": [],
    "tests_done": [],
}


def test_check_runs_test_and_reports(toolbox, fake, tmp_path):
    toolbox.capture_baseline("joint_test", {"joint": 2, "delta_deg": 5})
    fake.inject("friction", joint=2, factor=1.6)
    llm = ScriptedLLM([[("joint_test", {"joint": 2, "delta_deg": 5})], [("submit_report", REPORT)]])
    state = run_check(toolbox, llm, toolbox.hmi, tmp_path / "reports")

    assert state["report"]["root_cause"] == REPORT["root_cause"]
    saved = json.loads(open(state["report_path"]).read())
    assert saved["recordings"], "le test doit être enregistré"
    # le résultat du test renvoyé à l'agent contient les écarts, un graphique et les valeurs
    result = llm.requests[1]["messages"][-1]["content"][0]["content"]
    kinds = [b["type"] for b in result]
    assert kinds == ["text", "image", "text"]
    assert "joint_2_current_actual" in result[0]["text"]
    assert result[2]["text"].count("\n") > 50


def test_out_of_bounds_test_is_refused_without_moving(toolbox, fake, tmp_path):
    llm = ScriptedLLM([[("joint_test", {"joint": 1, "delta_deg": 45})], [("submit_report", REPORT)]])
    run_check(toolbox, llm, toolbox.hmi, tmp_path / "reports")
    result = llm.requests[1]["messages"][-1]["content"][0]["content"]
    assert result.startswith("REFUSÉ")
    assert fake.calls == []


def test_loop_mode_wakes_on_fault_and_asks_operator(toolbox, fake, tmp_path):
    events = toolbox.recorder.subscribe()
    llm = ScriptedLLM([[("submit_report", REPORT)]])
    graph = build_graph(toolbox, llm, toolbox.hmi, events=events, reports_dir=tmp_path / "reports", watch_timeout=5)
    fake.inject("fault", joint=2)
    state = graph.invoke({"mode": "loop"})
    assert state["alert"] is not None and "C204A3" in state["alert"].reason
    assert any(line.startswith("confirm Anomalie") for line in toolbox.hmi.log)
    assert state["report"]["root_cause"]
    # le premier message à l'agent contient le code d'erreur
    assert "C204A3" in llm.requests[0]["messages"][0]["content"]


def test_loop_mode_stops_when_operator_declines(toolbox, fake, tmp_path):
    toolbox.hmi.approve = False
    events = toolbox.recorder.subscribe()
    llm = ScriptedLLM([])
    graph = build_graph(toolbox, llm, toolbox.hmi, events=events, reports_dir=tmp_path / "reports", watch_timeout=5)
    fake.inject("fault")
    state = graph.invoke({"mode": "loop"})
    assert state["approved"] is False and llm.requests == []
