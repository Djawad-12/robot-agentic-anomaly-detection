"""Vérifications déterministes : des calculs simples et sûrs, sans IA.

Deux usages :
- `EventWatcher` lit le journal et les codes d'erreur en continu et lève une `Alert` qui
  réveille l'agent en mode boucle ;
- `compare` mesure l'écart entre un enregistrement et la référence de bon fonctionnement.
  Le résultat accompagne les données complètes envoyées au LLM : il oriente sa lecture, il
  ne la remplace pas.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .baseline import Baseline, ChannelStats
from .events import Alert, FaultCode, Level, LogLine, Severity
from .recorder import Recording

# Motifs du journal qui signalent une panne même sans code d'erreur.
CRITICAL_PATTERNS = [
    r"protective stop",
    r"emergency stop",
    r"lost communication",
    r"connection (lost|refused|reset)",
    r"fault",
    r"collision",
    r"overcurrent|over current",
]


class EventWatcher:
    """Transforme les lignes de journal et les codes d'erreur en alertes.

    Une même raison n'est signalée qu'une fois toutes les `cooldown_s` secondes, pour éviter
    de réveiller l'agent en rafale pendant une panne qui se répète.
    """

    def __init__(self, cooldown_s: float = 30.0):
        self.cooldown_s = cooldown_s
        self._last: dict[str, float] = {}
        self._patterns = [re.compile(p, re.IGNORECASE) for p in CRITICAL_PATTERNS]

    def feed(self, event: LogLine | FaultCode) -> Alert | None:
        alert = self._classify(event)
        if alert is None:
            return None
        if event.t - self._last.get(alert.reason, -math.inf) < self.cooldown_s:
            return None
        self._last[alert.reason] = event.t
        return alert

    def _classify(self, event: LogLine | FaultCode) -> Alert | None:
        if isinstance(event, FaultCode):
            sev = Severity.CRITICAL if event.level in (Level.ERROR, Level.FATAL) else Severity.WARNING
            return Alert(t=event.t, reason=f"code d'erreur {event.code}", severity=sev, evidence=[f"{event.code} : {event.message}"])
        if any(p.search(event.text) for p in self._patterns):
            return Alert(t=event.t, reason=f"journal : {event.text[:80]}", severity=Severity.CRITICAL, evidence=[event.text])
        if event.level in (Level.ERROR, Level.FATAL):
            return Alert(t=event.t, reason=f"erreur dans le journal ({event.source or 'machine'})", severity=Severity.WARNING, evidence=[event.text])
        return None


@dataclass
class Deviation:
    channel: str
    baseline: ChannelStats
    current: ChannelStats

    @property
    def rms_change(self) -> float:
        """Variation relative de la valeur efficace (RMS), en %."""
        ref = self.baseline.rms
        return float("nan") if not ref else 100.0 * (self.current.rms - ref) / ref

    @property
    def peak_change(self) -> float:
        ref = max(abs(self.baseline.max), abs(self.baseline.min))
        cur = max(abs(self.current.max), abs(self.current.min))
        return float("nan") if not ref else 100.0 * (cur - ref) / ref

    def describe(self) -> str:
        return (
            f"{self.channel} : RMS {self.current.rms:.4g} (référence {self.baseline.rms:.4g}, {self.rms_change:+.0f} %), "
            f"pic {self.peak_change:+.0f} %"
        )


def compare(rec: Recording, baseline: Baseline, threshold_pct: float = 15.0) -> list[Deviation]:
    """Voies dont la valeur efficace ou le pic s'écarte de plus de `threshold_pct` %, de la
    plus grande à la plus petite variation. Les voies quasi nulles dans la référence sont
    ignorées : un pourcentage n'y a pas de sens."""
    out = []
    for name, ref in baseline.stats.items():
        if name not in rec.channels or not math.isfinite(ref.rms) or abs(ref.rms) < 1e-6:
            continue
        dev = Deviation(name, ref, ChannelStats.of(rec.channels[name]))
        if abs(dev.rms_change) >= threshold_pct or abs(dev.peak_change) >= threshold_pct:
            out.append(dev)
    return sorted(out, key=lambda d: -max(abs(d.rms_change), abs(d.peak_change)))


def stream_gaps(rec: Recording, max_gap_s: float = 0.1) -> list[tuple[float, float]]:
    """Trous dans le flux de signaux (perte de communication) : (début relatif, durée)."""
    if len(rec.t) < 2:
        return [(0.0, rec.duration)] if rec.duration > max_gap_s else []
    dt = (rec.t[1:] - rec.t[:-1])
    return [(float(rec.t[i] - rec.t0), float(dt[i])) for i in range(len(dt)) if dt[i] > max_gap_s]
