"""Tests of the core: safety checks, alerts, recorder and settings."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from robot_agentic.config import load_settings
from robot_agentic.connectors.fake import FakeConnector
from robot_agentic.core import checks
from robot_agentic.core.events import FaultCode, Level, LogLine, Sample
from robot_agentic.core.recorder import Recorder, Recording
from robot_agentic.core.safety import SafetyPolicy, SafetyRefusal


def test_safety_refuses_out_of_bounds_and_missing(fake: FakeConnector) -> None:
    """An out-of-bounds or missing argument is refused, even if the operator approves everything."""
    spec = fake.tools()[0]
    policy = SafetyPolicy(ask_operator=lambda q: True)
    with pytest.raises(SafetyRefusal, match="outside the allowed range"):
        policy.check(spec, {"joint": 1, "delta_deg": 30})
    with pytest.raises(SafetyRefusal, match="required"):
        policy.check(spec, {"joint": 1})


def test_safety_requires_operator_and_pin(fake: FakeConnector) -> None:
    """A movement needs the operator's approval, then the right PIN if one is configured."""
    spec = fake.tools()[0]
    args = {"joint": 1, "delta_deg": 2}
    with pytest.raises(SafetyRefusal, match="refused"):
        SafetyPolicy(ask_operator=lambda q: False).check(spec, args)
    with pytest.raises(SafetyRefusal, match="PIN"):
        SafetyPolicy(ask_operator=lambda q: True, pin="1234", ask_pin=lambda: "0000").check(spec, args)
    SafetyPolicy(ask_operator=lambda q: True, pin="1234", ask_pin=lambda: "1234").check(spec, args)


def test_tool_schema_shows_the_safety_bounds(fake: FakeConnector) -> None:
    """The JSON schema shown to the LLM carries the same bounds as those enforced by the safety checks."""
    schema = fake.tools()[0].input_schema()
    assert schema["properties"]["delta_deg"]["minimum"] == -5 and schema["properties"]["delta_deg"]["maximum"] == 5
    assert schema["required"] == ["joint", "delta_deg"]


def test_watcher_alerts_on_fault_and_error_but_not_info() -> None:
    """A fault code and a log error give an alert; an info line does not; nor does a repetition."""
    w = checks.EventWatcher()
    assert w.feed(LogLine(text="Socket closed", level=Level.INFO)) is None
    assert w.feed(LogLine(text="Syntax error on line 1", level=Level.ERROR)) is not None
    alert = w.feed(FaultCode(code="C204A3", message="protective stop"))
    assert alert is not None and "C204A3" in alert.reason
    assert w.feed(FaultCode(code="C204A3", message="protective stop")) is None


def test_watcher_ignores_default_but_not_fault() -> None:
    """The "fault" pattern matches the word only: "default" in an info line raises nothing."""
    w = checks.EventWatcher()
    assert w.feed(LogLine(text="Loading default installation", level=Level.INFO)) is None
    assert w.feed(LogLine(text="Safety fault on joint 3", level=Level.INFO)) is not None


def test_recorder_ring_buffer_keeps_the_last_samples(fake: FakeConnector, tmp_path: Path) -> None:
    """Once full, the buffer keeps only the most recent samples, in time order, with NaN for missing channels."""
    rec = Recorder(fake, tmp_path, history_s=1.0, max_rate_hz=10.0)  # 10 rows
    for i in range(25):
        rec._add_sample(Sample(t=float(i), values={"a": float(i)} | ({"b": 1.0} if i == 24 else {})))
    window = rec.window(0.0, 100.0)
    assert window.t.tolist() == [float(i) for i in range(15, 25)]
    assert window.channels["a"].tolist() == [float(i) for i in range(15, 25)]
    assert np.isnan(window.channels["b"][0]) and window.channels["b"][-1] == 1.0


def test_recording_save_and_load(tmp_path: Path) -> None:
    """A saved recording reads back identically, log levels included."""
    rec = Recording(
        "r1", "test", "m", 0.0, 1.0, np.array([0.0, 0.5]), {"a": np.array([1.0, 2.0])}, logs=[LogLine(t=0.2, level=Level.ERROR, text="x")]
    )
    back = Recording.load(rec.save(tmp_path))
    assert back.channels["a"].tolist() == [1.0, 2.0] and back.logs[0].level is Level.ERROR


def test_stream_that_stops_for_good_is_a_cut() -> None:
    """A stream that stops before the end of the window (controller crash) shows as a cut at the end."""
    t = np.arange(0.0, 2.0, 0.002)
    rec = Recording("r", "crash", "m", 0.0, 15.0, t, {"a": np.zeros_like(t)})
    gaps = checks.stream_gaps(rec)
    assert len(gaps) == 1 and gaps[0][0] > 1.99 and gaps[0][1] > 12.9


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each setting is read from the variable of the same name in upper case, converted to its type."""
    monkeypatch.setenv("RECORDING_TOKEN_BUDGET", "1234")
    monkeypatch.setenv("DATA_DIR", "/tmp/x")
    s = load_settings()
    assert s.recording_token_budget == 1234 and s.data_dir == Path("/tmp/x")
