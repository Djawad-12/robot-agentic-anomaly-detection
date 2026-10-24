"""Vérifications déterministes : des calculs simples et sûrs, sans IA.

Deux usages :
- `EventWatcher` lit le journal et les codes d'erreur en continu et lève une `Alert` qui
  réveille l'agent en mode boucle ;
- `compare` mesure l'écart entre un enregistrement et la référence de bon fonctionnement,
  et `stream_gaps` repère les coupures du flux de signaux. Ces résultats accompagnent les
  données complètes envoyées au LLM : ils orientent sa lecture, ils ne la remplacent pas.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from .baseline import Baseline, ChannelStats
from .events import Alert, FaultCode, Level, LogLine, Severity
from .recorder import Recording

log = logging.getLogger(__name__)

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

    Règles, de la plus grave à la moins grave :
    - un code d'erreur donne toujours une alerte (critique s'il est de niveau ERROR ou FATAL) ;
    - une ligne de journal qui contient un motif de `CRITICAL_PATTERNS` donne une alerte critique ;
    - une ligne de journal de niveau ERROR ou FATAL donne une alerte d'avertissement.
    Une même raison n'est signalée qu'une fois toutes les `cooldown_s` secondes, pour ne pas
    réveiller l'agent en rafale pendant une panne qui se répète.

    Args:
        cooldown_s: délai minimal entre deux alertes de même raison, en secondes.
    """

    def __init__(self, cooldown_s: float = 30.0) -> None:
        """Prépare les motifs et la mémoire des dernières alertes."""
        self.cooldown_s = cooldown_s
        self._last: dict[str, float] = {}
        self._patterns = [re.compile(p, re.IGNORECASE) for p in CRITICAL_PATTERNS]

    def feed(self, event: LogLine | FaultCode) -> Alert | None:
        """Examine un événement et renvoie une alerte s'il en justifie une, None sinon."""
        if isinstance(event, FaultCode):
            severity = Severity.CRITICAL if event.level in (Level.ERROR, Level.FATAL) else Severity.WARNING
            alert = Alert(t=event.t, reason=f"code d'erreur {event.code}", severity=severity, evidence=[f"{event.code} : {event.message}"])
        elif any(p.search(event.text) for p in self._patterns):
            alert = Alert(t=event.t, reason=f"journal : {event.text[:80]}", severity=Severity.CRITICAL, evidence=[event.text])
        elif event.level in (Level.ERROR, Level.FATAL):
            alert = Alert(
                t=event.t, reason=f"erreur dans le journal ({event.source or 'machine'})", severity=Severity.WARNING, evidence=[event.text]
            )
        else:
            return None
        if event.t - self._last.get(alert.reason, -math.inf) < self.cooldown_s:
            log.debug("alerte ignorée (déjà signalée il y a moins de %.0f s) : %s", self.cooldown_s, alert.reason)
            return None
        self._last[alert.reason] = event.t
        log.info("alerte %s : %s", alert.severity.value, alert.reason)
        return alert


@dataclass
class Deviation:
    """Écart d'une voie entre un test et sa référence."""

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
        """Variation relative de la plus grande valeur absolue, en %."""
        ref = max(abs(self.baseline.max), abs(self.baseline.min))
        cur = max(abs(self.current.max), abs(self.current.min))
        return float("nan") if not ref else 100.0 * (cur - ref) / ref

    def describe(self) -> str:
        """Une ligne lisible, reprise telle quelle dans le message envoyé à l'agent."""
        return (
            f"{self.channel} : RMS {self.current.rms:.4g} (référence {self.baseline.rms:.4g}, {self.rms_change:+.0f} %), "
            f"pic {self.peak_change:+.0f} %"
        )


def compare(rec: Recording, baseline: Baseline, threshold_pct: float = 15.0) -> list[Deviation]:
    """Voies dont la valeur efficace ou le pic s'écarte de plus de `threshold_pct` % de la référence.

    Les voies quasi nulles dans la référence sont ignorées : un pourcentage n'y a pas de sens.

    Returns:
        Les écarts, du plus grand au plus petit.
    """
    out = []
    for name, ref in baseline.stats.items():
        if name not in rec.channels or not math.isfinite(ref.rms) or abs(ref.rms) < 1e-6:
            continue
        dev = Deviation(name, ref, ChannelStats.of(rec.channels[name]))
        if abs(dev.rms_change) >= threshold_pct or abs(dev.peak_change) >= threshold_pct:
            out.append(dev)
    out.sort(key=lambda d: -max(abs(d.rms_change), abs(d.peak_change)))
    log.debug("comparaison de %s à la référence %s : %d voie(s) en écart", rec.id, baseline.test, len(out))
    return out


def stream_gaps(rec: Recording, max_gap_s: float = 0.1) -> list[tuple[float, float]]:
    """Trous de plus de `max_gap_s` secondes dans le flux de signaux (perte de communication).

    Returns:
        Pour chaque trou : (début relatif à la fenêtre, durée), en secondes. Un enregistrement
        sans échantillon compte comme un seul trou couvrant toute la fenêtre.
    """
    if len(rec.t) < 2:
        return [(0.0, rec.duration)] if rec.duration > max_gap_s else []
    dt = rec.t[1:] - rec.t[:-1]
    return [(float(rec.t[i] - rec.t0), float(dt[i])) for i in range(len(dt)) if dt[i] > max_gap_s]
