"""Tests du cœur : garde-fous, alertes et comparaison à la référence."""

from __future__ import annotations

import pytest

from robot_agentic.agent.tools import Toolbox
from robot_agentic.connectors.fake import FakeConnector
from robot_agentic.core import checks
from robot_agentic.core.baseline import Baseline
from robot_agentic.core.events import FaultCode, Level, LogLine
from robot_agentic.core.safety import SafetyPolicy, SafetyRefusal


def test_safety_refuses_out_of_bounds(fake: FakeConnector) -> None:
    """Un argument hors bornes est refusé, même si l'opérateur accepte tout."""
    spec = fake.tools()[0]
    policy = SafetyPolicy(ask_operator=lambda q: True)
    with pytest.raises(SafetyRefusal, match="hors des limites"):
        policy.check(spec, {"joint": 1, "delta_deg": 30})


def test_safety_requires_operator_and_pin(fake: FakeConnector) -> None:
    """Un mouvement exige l'accord de l'opérateur, puis le bon code PIN s'il est configuré."""
    spec = fake.tools()[0]
    with pytest.raises(SafetyRefusal, match="refusé"):
        SafetyPolicy(ask_operator=lambda q: False).check(spec, {"joint": 1, "delta_deg": 2})
    with pytest.raises(SafetyRefusal, match="PIN"):
        SafetyPolicy(ask_operator=lambda q: True, pin="1234", ask_pin=lambda: "0000").check(spec, {"joint": 1, "delta_deg": 2})
    SafetyPolicy(ask_operator=lambda q: True, pin="1234", ask_pin=lambda: "1234").check(spec, {"joint": 1, "delta_deg": 2})


def test_watcher_alerts_on_fault_and_error_but_not_info() -> None:
    """Un code d'erreur et une erreur de journal donnent une alerte ; une info, non ; une répétition, non plus."""
    w = checks.EventWatcher()
    assert w.feed(LogLine(text="Socket closed", level=Level.INFO)) is None
    assert w.feed(LogLine(text="Syntax error on line 1", level=Level.ERROR)) is not None
    alert = w.feed(FaultCode(code="C204A3", message="protective stop"))
    assert alert is not None and "C204A3" in alert.reason
    assert w.feed(FaultCode(code="C204A3", message="protective stop")) is None


def test_baseline_comparison_finds_friction(toolbox: Toolbox, fake: FakeConnector) -> None:
    """Un frottement sur l'articulation 2 ressort en tête des écarts, sans toucher aux autres articulations."""
    rec_ok, _ = toolbox.recorder.record(lambda: fake.call_tool("joint_test", {"joint": 2, "delta_deg": 5}), "ok", before=0.2, after=0.2)
    base = Baseline.from_recording(rec_ok, "joint_test:2:5")
    fake.inject("friction", joint=2, factor=1.6)
    rec_bad, _ = toolbox.recorder.record(lambda: fake.call_tool("joint_test", {"joint": 2, "delta_deg": 5}), "bad", before=0.2, after=0.2)
    deviations = checks.compare(rec_bad, base)
    assert deviations and deviations[0].channel in {"joint_2_current_actual", "joint_2_motor_temperature"}
    assert all(not d.channel.startswith("joint_0") for d in deviations)
