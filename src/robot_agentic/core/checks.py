"""Deterministic checks: simple, safe computations, without AI.

- `EventWatcher` reads the log and the fault codes continuously and raises an `Alert` that
  wakes the agent up in loop mode;
- `stream_gaps` finds the cuts in the signal stream (communication loss).

The rules on signal values (see summary.md, section 3) will be added here.
"""

from __future__ import annotations

import logging
import math
import re

import numpy as np

from .events import Alert, FaultCode, Level, LogLine, Severity
from .recorder import Recording

log = logging.getLogger(__name__)

# Log patterns that mean a failure even without a fault code. Word boundaries keep
# "fault" from matching "default".
CRITICAL_PATTERNS = [
    r"protective stop",
    r"emergency stop",
    r"lost communication",
    r"connection (lost|refused|reset)",
    r"\bfault\b",
    r"collision",
    r"overcurrent|over current",
]


class EventWatcher:
    """Turns log lines and fault codes into alerts.

    Rules, from most to least severe:
    - a fault code always gives an alert (critical if its level is ERROR or FATAL);
    - a log line containing a pattern of `CRITICAL_PATTERNS` gives a critical alert;
    - a log line of level ERROR or FATAL gives a warning alert.
    The same reason is reported at most once every `cooldown_s` seconds, so that a repeating
    failure does not wake the agent up in bursts.

    Args:
        cooldown_s: minimal delay between two alerts with the same reason, in seconds.
    """

    def __init__(self, cooldown_s: float = 30.0) -> None:
        """Compile the patterns and prepare the memory of the last alerts."""
        self.cooldown_s = cooldown_s
        self._last: dict[str, float] = {}
        self._patterns = [re.compile(p, re.IGNORECASE) for p in CRITICAL_PATTERNS]

    def feed(self, event: LogLine | FaultCode) -> Alert | None:
        """Examine one event and return an alert if it deserves one, None otherwise."""
        if isinstance(event, FaultCode):
            severity = Severity.CRITICAL if event.level in (Level.ERROR, Level.FATAL) else Severity.WARNING
            alert = Alert(t=event.t, reason=f"fault code {event.code}", severity=severity, evidence=[f"{event.code}: {event.message}"])
        elif any(p.search(event.text) for p in self._patterns):
            alert = Alert(t=event.t, reason=f"log: {event.text[:80]}", severity=Severity.CRITICAL, evidence=[event.text])
        elif event.level in (Level.ERROR, Level.FATAL):
            alert = Alert(t=event.t, reason=f"error in the log ({event.source or 'machine'})", evidence=[event.text])
        else:
            return None
        if event.t - self._last.get(alert.reason, -math.inf) < self.cooldown_s:
            log.debug("alert skipped (already reported less than %.0f s ago): %s", self.cooldown_s, alert.reason)
            return None
        self._last[alert.reason] = event.t
        log.info("%s alert: %s", alert.severity.value, alert.reason)
        return alert


def stream_gaps(rec: Recording, max_gap_s: float = 0.1) -> list[tuple[float, float]]:
    """Holes longer than `max_gap_s` seconds in the signal stream (communication loss).

    The start and the end of the window count too: a stream that stops for good (controller
    crash) leaves a hole between its last sample and the end of the recording.

    Returns:
        For each hole: (start relative to the window, duration), in seconds. A recording
        without any sample counts as one hole covering the whole window.
    """
    edges = np.concatenate([[rec.t0], rec.t, [rec.t1]])
    dt = np.diff(edges)
    return [(float(edges[i] - rec.t0), float(dt[i])) for i in np.flatnonzero(dt > max_gap_s)]
