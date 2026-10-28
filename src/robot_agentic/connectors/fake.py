"""Fake connector: a machine simulated in Python, to test the agent without URSim.

It produces realistic signals (six joints, current proportional to the effort) and can
simulate a fault: `inject("friction", joint=2)` raises the effort of one joint while it
moves, `inject("fault")` emits a fault code. The automatic tests use it: fast, free and
reproducible.
"""

from __future__ import annotations

import logging
import math
import queue
import time
from collections.abc import Iterator
from typing import Any

from ..core.events import FaultCode, Level, LogLine, Param, Sample, ToolSpec
from ..core.interfaces import Connector

log = logging.getLogger(__name__)

RATE_HZ = 100


class FakeConnector(Connector):
    """Simulated six-joint arm; its movements are computed in the calling thread.

    Attributes:
        q: joint positions (rad).
        qd: joint speeds (rad/s).
        friction: effort factor per joint (1.0 = healthy).
        calls: actions received, to check in the tests what was run.
    """

    name = "fake_arm"

    def __init__(self) -> None:
        """Put the arm in a rest position, without any fault."""
        self.q: list[float] = [0.0, -1.57, 1.57, -1.57, -1.57, 0.0]
        self.qd: list[float] = [0.0] * 6
        self.friction: list[float] = [1.0] * 6
        self.log_q: queue.Queue[LogLine] = queue.Queue()
        self.fault_q: queue.Queue[FaultCode] = queue.Queue()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def inject(self, kind: str, joint: int = 2, factor: float = 1.4) -> None:
        """Simulate a fault.

        Args:
            kind: "friction" (effort multiplied by `factor` on `joint`, with a warning in the
                log) or "fault" (fault code C204A3 on `joint`).
            joint: affected joint.
            factor: effort factor for "friction".

        Raises:
            ValueError: if `kind` is unknown.
        """
        log.info("simulated fault: %s on joint %d", kind, joint)
        if kind == "friction":
            self.friction[joint] = factor
            self.log_q.put(LogLine(level=Level.WARNING, source="controller", text=f"Joint {joint}: high following error"))
        elif kind == "fault":
            self.fault_q.put(FaultCode(code="C204A3", message=f"[JOINT{joint}] Protective stop: joint position deviates from target"))
        else:
            raise ValueError(kind)

    def signals(self) -> Iterator[Sample]:
        """Samples at 100 Hz: the effort depends on gravity, speed and friction."""
        while True:
            values: dict[str, float] = {}
            for j in range(6):
                values[f"joint_{j}_q_actual"] = self.q[j]
                values[f"joint_{j}_qd_actual"] = self.qd[j]
                values[f"joint_{j}_current_actual"] = 0.4 + 0.3 * math.cos(self.q[j]) + 2.0 * abs(self.qd[j]) * self.friction[j]
                values[f"joint_{j}_motor_temperature"] = 30.0 + 2.0 * (self.friction[j] - 1.0)
            yield Sample(values=values)
            time.sleep(1 / RATE_HZ)

    def logs(self) -> Iterator[LogLine]:
        """Log lines produced by `inject`."""
        while True:
            yield self.log_q.get()

    def faults(self) -> Iterator[FaultCode]:
        """Fault codes produced by `inject`."""
        while True:
            yield self.fault_q.get()

    def docs(self) -> list[str]:
        """No documentation."""
        return []

    def tools(self) -> list[ToolSpec]:
        """A single test: the round trip of one joint, bounded to ±5°."""
        return [
            ToolSpec(
                name="joint_test",
                description="Turns one joint by delta_deg degrees, then brings it back.",
                params={"joint": Param("integer", 0, 5, "joint index"), "delta_deg": Param("number", -5, 5, "degrees")},
                moves_machine=True,
            )
        ]

    def call_tool(self, name: str, args: dict[str, Any]) -> str:
        """Do the joint's round trip at 0.3 rad/s, in real time.

        Raises:
            ValueError: if the tool does not exist.
        """
        self.calls.append((name, args))
        if name != "joint_test":
            raise ValueError(f"unknown tool: {name}")
        j, d = int(args["joint"]), math.radians(float(args["delta_deg"]))
        start = self.q[j]
        for target in (start + d, start):
            speed = 0.3 if target > self.q[j] else -0.3
            while abs(target - self.q[j]) > 0.004:
                self.qd[j] = speed
                self.q[j] += speed * 0.01
                time.sleep(0.01)
            self.qd[j] = 0.0
            self.q[j] = target
        return f"joint {j} {float(args['delta_deg']):+}°: finished"

    def stop(self) -> None:
        """Cancel every speed."""
        self.qd = [0.0] * 6

    def health(self) -> list[tuple[bool, str]]:
        """Always healthy."""
        return [(True, "simulated machine")]

    def snapshot(self) -> dict[str, Any]:
        """Fixed state: running, normal safety."""
        return {"mode": "RUNNING", "safety": "NORMAL"}
