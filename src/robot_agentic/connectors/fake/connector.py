"""Faux connecteur : une machine simulée en Python, pour tester l'agent sans URSim.

Il produit des signaux réalistes (six articulations, courant proportionnel à l'effort) et
peut simuler une panne : `inject("friction", joint=2)` augmente l'effort d'une articulation
pendant ses mouvements, `inject("fault")` émet un code d'erreur. Les tests automatiques et
les essais de prompts l'utilisent : rapides, gratuits et reproductibles.
"""

from __future__ import annotations

import math
import queue
import time
from collections.abc import Iterator
from typing import Any

from ...core.events import FaultCode, Level, LogLine, Sample, ToolSpec
from ...core.interfaces import Connector

RATE_HZ = 100


class FakeConnector(Connector):
    name = "fake_arm"

    def __init__(self) -> None:
        self.q = [0.0, -1.57, 1.57, -1.57, -1.57, 0.0]
        self.qd = [0.0] * 6
        self.friction = [1.0] * 6
        self.log_q: queue.Queue[LogLine] = queue.Queue()
        self.fault_q: queue.Queue[FaultCode] = queue.Queue()
        self.calls: list[tuple[str, dict]] = []

    # --- Pannes simulées ------------------------------------------------------------------

    def inject(self, kind: str, joint: int = 2, factor: float = 1.4) -> None:
        if kind == "friction":
            self.friction[joint] = factor
            self.log_q.put(LogLine(level=Level.WARNING, source="controller", text=f"Joint {joint}: high following error"))
        elif kind == "fault":
            self.fault_q.put(FaultCode(code="C204A3", message=f"[JOINT{joint}] Protective stop: joint position deviates from target"))
        else:
            raise ValueError(kind)

    # --- Flux ---------------------------------------------------------------------------

    def signals(self) -> Iterator[Sample]:
        while True:
            values: dict[str, float] = {}
            for j in range(6):
                effort = 0.4 + 0.3 * math.cos(self.q[j]) + 2.0 * abs(self.qd[j]) * self.friction[j]
                values[f"joint_{j}_q_actual"] = self.q[j]
                values[f"joint_{j}_qd_actual"] = self.qd[j]
                values[f"joint_{j}_current_actual"] = effort
                values[f"joint_{j}_motor_temperature"] = 30.0 + 2.0 * (self.friction[j] - 1.0)
            yield Sample(values=values)
            time.sleep(1 / RATE_HZ)

    def logs(self) -> Iterator[LogLine]:
        while True:
            yield self.log_q.get()

    def faults(self) -> Iterator[FaultCode]:
        while True:
            yield self.fault_q.get()

    def docs(self) -> list[str]:
        return []

    # --- Actions ------------------------------------------------------------------------

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="joint_test",
                description="Fait tourner une articulation de delta_deg degrés puis la ramène.",
                input_schema={
                    "type": "object",
                    "properties": {"joint": {"type": "integer"}, "delta_deg": {"type": "number"}},
                    "required": ["joint", "delta_deg"],
                    "additionalProperties": False,
                },
                moves_machine=True,
                limits={"joint": (0, 5), "delta_deg": (-5, 5)},
            )
        ]

    def call_tool(self, name: str, args: dict[str, Any]) -> str:
        self.calls.append((name, args))
        if name != "joint_test":
            raise ValueError(f"outil inconnu : {name}")
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
        return f"articulation {j} {args['delta_deg']:+}° : terminé"

    def stop(self) -> None:
        self.qd = [0.0] * 6

    def health(self) -> dict[str, Any]:
        return {"ok": True, "machine": "simulée"}

    def snapshot(self) -> dict[str, Any]:
        return {"mode": "RUNNING", "sécurité": "NORMAL"}
