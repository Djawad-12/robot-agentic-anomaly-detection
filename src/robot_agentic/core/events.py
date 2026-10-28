"""Data types shared by the whole project.

Each connector translates what its machine produces into these types: the agent, the checks
and the recorder only know them. Every instant `t` is in seconds since the Unix epoch
(`time.time()`), taken when the data is received on this computer.

Plain dataclasses rather than validated models: signals arrive at 500 Hz, and each connector
already parses its own format strictly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(slots=True, kw_only=True)
class Sample:
    """One instant of the signal stream: every channel measured at the same moment.

    `values` maps a channel name (e.g. "joint_2_current_actual") to its value. Grouping the
    channels by instant keeps a recording aligned in time without resampling.
    """

    t: float = field(default_factory=time.time)
    values: dict[str, float]


class Level(StrEnum):
    """Severity of a log line or fault code, from lowest to highest."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"


@dataclass(kw_only=True)
class LogLine:
    """One line of the machine's log. `source` says which component wrote it."""

    t: float = field(default_factory=time.time)
    level: Level = Level.INFO
    source: str = ""
    text: str


@dataclass(kw_only=True)
class FaultCode:
    """A fault code raised by the controller (e.g. C204A3 on a UR robot)."""

    t: float = field(default_factory=time.time)
    code: str
    message: str = ""
    level: Level = Level.ERROR


class Severity(StrEnum):
    """Severity of an alert raised by the deterministic checks."""

    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(kw_only=True)
class Alert:
    """Anomaly detected by the deterministic checks; wakes the agent up in loop mode.

    `evidence` holds the log lines or fault codes that triggered it, passed to the agent as is.
    """

    t: float = field(default_factory=time.time)
    reason: str
    severity: Severity = Severity.WARNING
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Param:
    """A numeric argument of a machine action, with its allowed range.

    The range is both shown to the LLM (JSON schema `minimum`/`maximum`) and enforced by
    `core.safety`: declaring it once keeps the two from ever disagreeing.

    Attributes:
        kind: JSON type, "number" or "integer".
        low: smallest allowed value.
        high: largest allowed value.
        description: unit and meaning, shown to the LLM.
    """

    kind: str
    low: float
    high: float
    description: str


@dataclass(kw_only=True)
class ToolSpec:
    """An action a connector offers to the agent (e.g. move one joint of the robot).

    `moves_machine` means the action sets the machine in motion: it then goes through the
    safety checks and the operator's approval, and it is recorded. Every parameter is
    required; a value outside its range is refused by `core.safety`, never silently clamped.
    """

    name: str
    description: str
    params: dict[str, Param] = field(default_factory=dict)
    moves_machine: bool = False

    def input_schema(self) -> dict:
        """JSON schema of the arguments, in the format of the Claude API."""
        return {
            "type": "object",
            "properties": {
                k: {"type": p.kind, "minimum": p.low, "maximum": p.high, "description": p.description} for k, p in self.params.items()
            },
            "required": list(self.params),
            "additionalProperties": False,
        }
