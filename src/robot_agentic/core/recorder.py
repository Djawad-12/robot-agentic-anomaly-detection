"""Enregistreur : garde en mémoire les derniers instants des trois flux temps réel et produit
des enregistrements alignés dans le temps.

Le `Recorder` lit en continu les signaux, le journal et les codes d'erreur dans des tampons
circulaires. Un enregistrement est une simple découpe de ces tampons entre deux instants :
on peut donc enregistrer un test (avant, pendant, après) sans rien rater du début.
"""

from __future__ import annotations

import gzip
import json
import queue
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .events import FaultCode, LogLine, Sample
from .interfaces import Connector


@dataclass
class Recording:
    """Signaux, journal et codes d'erreur entre deux instants."""

    id: str
    label: str
    machine: str
    t0: float
    t1: float
    t: np.ndarray
    channels: dict[str, np.ndarray]
    logs: list[LogLine] = field(default_factory=list)
    faults: list[FaultCode] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.t1 - self.t0

    @property
    def rate_hz(self) -> float:
        return (len(self.t) - 1) / (self.t[-1] - self.t[0]) if len(self.t) > 1 else 0.0

    def select(self, channels: list[str] | None = None) -> dict[str, np.ndarray]:
        if not channels:
            return self.channels
        missing = [c for c in channels if c not in self.channels]
        if missing:
            raise KeyError(f"voies inconnues : {missing}")
        return {c: self.channels[c] for c in channels}

    def resample(self, hz: float, channels: list[str] | None = None) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Moyenne par fenêtres pour ramener l'enregistrement à `hz` points par seconde.

        Une moyenne (et non un simple saut d'échantillons) conserve l'énergie des pics courts.
        """
        data = self.select(channels)
        if len(self.t) == 0 or hz >= self.rate_hz:
            return self.t - self.t0, data
        step = max(1, round(self.rate_hz / hz))
        n = len(self.t) // step * step
        t = self.t[:n].reshape(-1, step).mean(axis=1) - self.t0
        return t, {k: v[:n].reshape(-1, step).mean(axis=1) for k, v in data.items()}

    # --- Sauvegarde --------------------------------------------------------------------

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.id}.json.gz"
        payload = {
            "id": self.id,
            "label": self.label,
            "machine": self.machine,
            "t0": self.t0,
            "t1": self.t1,
            "t": self.t.tolist(),
            "channels": {k: v.tolist() for k, v in self.channels.items()},
            "logs": [line.model_dump(mode="json") for line in self.logs],
            "faults": [f.model_dump(mode="json") for f in self.faults],
            "meta": self.meta,
        }
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return path

    @classmethod
    def load(cls, path: Path) -> Recording:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            p = json.load(fh)
        return cls(
            id=p["id"],
            label=p["label"],
            machine=p["machine"],
            t0=p["t0"],
            t1=p["t1"],
            t=np.asarray(p["t"]),
            channels={k: np.asarray(v) for k, v in p["channels"].items()},
            logs=[LogLine(**x) for x in p["logs"]],
            faults=[FaultCode(**x) for x in p["faults"]],
            meta=p.get("meta", {}),
        )


class Recorder:
    """Lit les flux du connecteur en continu et découpe des enregistrements à la demande."""

    def __init__(self, connector: Connector, directory: Path, history_s: float = 120.0, max_rate_hz: float = 500.0):
        self.connector = connector
        self.directory = directory
        self._samples: deque[Sample] = deque(maxlen=int(history_s * max_rate_hz))
        self._logs: deque[LogLine] = deque(maxlen=5000)
        self._faults: deque[FaultCode] = deque(maxlen=1000)
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self.errors: dict[str, str] = {}

    # --- Lecture continue ----------------------------------------------------------------

    def start(self) -> Recorder:
        if self._threads:
            return self
        for name, stream, sink in (
            ("signals", self.connector.signals, self._samples),
            ("logs", self.connector.logs, self._logs),
            ("faults", self.connector.faults, self._faults),
        ):
            th = threading.Thread(target=self._pump, args=(name, stream, sink), name=f"recorder-{name}", daemon=True)
            th.start()
            self._threads.append(th)
        return self

    def _pump(self, name: str, stream: Callable, sink: deque) -> None:
        # Un flux qui tombe (machine arrêtée, connexion perdue) est relancé : la perte de
        # données est elle-même une information, notée dans `errors`.
        while True:
            try:
                for item in stream():
                    with self._lock:
                        sink.append(item)
                    if name != "signals":
                        for q in self._subscribers:
                            q.put(item)
                self.errors[name] = "flux terminé"
            except Exception as exc:  # noqa: BLE001 - on veut survivre à toute erreur réseau
                self.errors[name] = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0)

    def subscribe(self) -> queue.Queue:
        """File des nouvelles lignes de journal et des nouveaux codes d'erreur (mode boucle)."""
        q: queue.Queue = queue.Queue()
        self._subscribers.append(q)
        return q

    # --- Découpe ----------------------------------------------------------------------

    def last_sample_age(self) -> float | None:
        with self._lock:
            return time.time() - self._samples[-1].t if self._samples else None

    def window(self, t0: float, t1: float, label: str = "", meta: dict | None = None) -> Recording:
        with self._lock:
            samples = [s for s in self._samples if t0 <= s.t <= t1]
            logs = [x for x in self._logs if t0 <= x.t <= t1]
            faults = [x for x in self._faults if t0 <= x.t <= t1]
        names = sorted({k for s in samples for k in s.values})
        channels = {k: np.array([s.values.get(k, np.nan) for s in samples]) for k in names}
        return Recording(
            id=time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4],
            label=label,
            machine=self.connector.name,
            t0=t0,
            t1=t1,
            t=np.array([s.t for s in samples]),
            channels=channels,
            logs=logs,
            faults=faults,
            meta=meta or {},
        )

    def latest(self, seconds: float, label: str = "derniers instants") -> Recording:
        t1 = time.time()
        return self.window(t1 - seconds, t1, label)

    def record(self, action: Callable[[], Any], label: str, before: float = 1.0, after: float = 1.5, meta: dict | None = None) -> tuple[Recording, Any]:
        """Enregistre tous les flux autour d'une action : `before` secondes avant, puis
        pendant l'action, puis `after` secondes après. Sauvegarde et renvoie l'enregistrement."""
        t0 = time.time() - before
        result = action()
        time.sleep(after)
        rec = self.window(t0, time.time(), label, meta)
        rec.save(self.directory)
        return rec, result

    def load(self, recording_id: str) -> Recording:
        return Recording.load(self.directory / f"{recording_id}.json.gz")
