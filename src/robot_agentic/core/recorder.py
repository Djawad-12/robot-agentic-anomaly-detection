"""Enregistreur : mémorise les trois flux temps réel et en découpe des enregistrements alignés.

Le `Recorder` lit en continu les signaux, le journal et les codes d'erreur dans des tampons
circulaires. Un enregistrement est une simple découpe de ces tampons entre deux instants :
on peut donc enregistrer un test (avant, pendant, après) sans rien rater du début.
"""

from __future__ import annotations

import gzip
import json
import logging
import queue
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .events import FaultCode, LogLine, Sample
from .interfaces import Connector

log = logging.getLogger(__name__)


@dataclass
class Recording:
    """Signaux, journal et codes d'erreur entre deux instants.

    `t` contient l'instant de chaque échantillon ; chaque tableau de `channels` a la même
    longueur que `t`. Une voie absente d'un échantillon vaut NaN. `meta["test"]` identifie
    le test enregistré, pour retrouver sa référence.
    """

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
        """Durée de la fenêtre enregistrée, en secondes."""
        return self.t1 - self.t0

    @property
    def rate_hz(self) -> float:
        """Rythme moyen des échantillons, en Hz (0 s'il y en a moins de deux)."""
        return (len(self.t) - 1) / (self.t[-1] - self.t[0]) if len(self.t) > 1 else 0.0

    def resample(self, hz: float, channels: list[str] | None = None) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Ramène l'enregistrement à `hz` points par seconde, par moyenne sur des fenêtres.

        Une moyenne (et non un simple saut d'échantillons) conserve l'énergie des pics courts.
        Si `hz` dépasse le rythme d'origine, les données sont rendues telles quelles.

        Args:
            hz: rythme voulu.
            channels: voies à garder ; toutes si None ou vide.

        Returns:
            Les instants, relatifs au début de la fenêtre (s), et les voies rééchantillonnées.

        Raises:
            KeyError: si une voie demandée n'existe pas dans l'enregistrement.
        """
        if channels:
            missing = [c for c in channels if c not in self.channels]
            if missing:
                raise KeyError(f"voies inconnues : {missing}")
            data = {c: self.channels[c] for c in channels}
        else:
            data = self.channels
        if len(self.t) == 0 or hz >= self.rate_hz:
            return self.t - self.t0, data
        step = max(1, round(self.rate_hz / hz))
        n = len(self.t) // step * step
        t = self.t[:n].reshape(-1, step).mean(axis=1) - self.t0
        return t, {k: v[:n].reshape(-1, step).mean(axis=1) for k, v in data.items()}

    def save(self, directory: Path) -> Path:
        """Écrit l'enregistrement dans `directory/<id>.json.gz` et renvoie le chemin."""
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
        log.debug("enregistrement %s sauvegardé (%d points, %d voies)", self.id, len(self.t), len(self.channels))
        return path

    @classmethod
    def load(cls, path: Path) -> Recording:
        """Relit un enregistrement écrit par `save`.

        Raises:
            FileNotFoundError: si le fichier n'existe pas.
        """
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
    """Lit les flux du connecteur en continu et découpe des enregistrements à la demande.

    Trois fils d'exécution (signaux, journal, codes d'erreur) remplissent des tampons
    circulaires. Un flux qui s'interrompt (machine arrêtée, connexion perdue) est relancé
    toutes les secondes ; la cause est gardée dans `errors` et journalisée, car une perte de
    données est elle-même une information pour le diagnostic.

    Args:
        connector: la machine dont on lit les flux.
        directory: dossier où `record` sauvegarde les enregistrements.
        history_s: profondeur des tampons, en secondes.
        max_rate_hz: rythme maximal attendu des signaux, pour dimensionner le tampon.
    """

    def __init__(self, connector: Connector, directory: Path, history_s: float = 120.0, max_rate_hz: float = 500.0) -> None:
        """Prépare les tampons ; la lecture ne commence qu'avec `start`."""
        self.connector = connector
        self.directory = directory
        self._samples: deque[Sample] = deque(maxlen=int(history_s * max_rate_hz))
        self._logs: deque[LogLine] = deque(maxlen=5000)
        self._faults: deque[FaultCode] = deque(maxlen=1000)
        self._subscribers: list[queue.Queue[LogLine | FaultCode]] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self.errors: dict[str, str] = {}

    def start(self) -> Recorder:
        """Lance la lecture des trois flux en arrière-plan (sans effet si elle tourne déjà)."""
        if self._threads:
            return self
        streams: list[tuple[str, Callable[[], Iterator[Any]], deque[Any]]] = [
            ("signals", self.connector.signals, self._samples),
            ("logs", self.connector.logs, self._logs),
            ("faults", self.connector.faults, self._faults),
        ]
        for name, stream, sink in streams:
            th = threading.Thread(target=self._pump, args=(name, stream, sink), name=f"recorder-{name}", daemon=True)
            th.start()
            self._threads.append(th)
        log.info("lecture des flux de %s démarrée", self.connector.name)
        return self

    def _pump(self, name: str, stream: Callable[[], Iterator[Any]], sink: deque[Any]) -> None:
        """Boucle d'un fil de lecture : remplit le tampon, sans jamais s'arrêter.

        Les journaux et codes d'erreur sont aussi poussés aux files d'abonnement (`subscribe`).
        """
        while True:
            try:
                for item in stream():
                    with self._lock:
                        sink.append(item)
                    if name != "signals":
                        for q in self._subscribers:
                            q.put(item)
                    if name in self.errors:
                        log.info("flux %s rétabli", name)
                        del self.errors[name]
                self.errors[name] = "flux terminé"
            except Exception as exc:  # noqa: BLE001 - toute erreur réseau ou de processus doit être survécue
                self.errors[name] = f"{type(exc).__name__}: {exc}"
            log.warning("flux %s interrompu (%s), nouvelle tentative dans 1 s", name, self.errors[name])
            time.sleep(1.0)

    def subscribe(self) -> queue.Queue[LogLine | FaultCode]:
        """File qui reçoit chaque nouvelle ligne de journal et chaque nouveau code d'erreur."""
        q: queue.Queue[LogLine | FaultCode] = queue.Queue()
        self._subscribers.append(q)
        return q

    def last_sample_age(self) -> float | None:
        """Âge du dernier échantillon reçu, en secondes (None si aucun n'est encore arrivé)."""
        with self._lock:
            return time.time() - self._samples[-1].t if self._samples else None

    def window(self, t0: float, t1: float, label: str = "", meta: dict[str, Any] | None = None) -> Recording:
        """Découpe les tampons entre `t0` et `t1` en un enregistrement (non sauvegardé)."""
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
        """Enregistrement des `seconds` dernières secondes (non sauvegardé)."""
        t1 = time.time()
        return self.window(t1 - seconds, t1, label)

    def record(
        self, action: Callable[[], Any], label: str, before: float = 1.0, after: float = 1.5, meta: dict[str, Any] | None = None
    ) -> tuple[Recording, Any]:
        """Enregistre tous les flux autour d'une action, puis sauvegarde l'enregistrement.

        Args:
            action: fonction sans argument qui déclenche le test et attend sa fin.
            label: description lisible du test.
            before: secondes gardées avant l'action, pour voir l'état de départ.
            after: secondes attendues après l'action, pour voir le retour au repos.
            meta: métadonnées gardées avec l'enregistrement (dont `test`).

        Returns:
            L'enregistrement et la valeur renvoyée par `action`.
        """
        t0 = time.time() - before
        log.info("enregistrement : %s", label)
        result = action()
        time.sleep(after)
        rec = self.window(t0, time.time(), label, meta)
        rec.save(self.directory)
        log.info(
            "enregistrement %s : %.1f s, %d points, %d lignes de journal, %d codes d'erreur",
            rec.id,
            rec.duration,
            len(rec.t),
            len(rec.logs),
            len(rec.faults),
        )
        return rec, result

    def load(self, recording_id: str) -> Recording:
        """Relit un enregistrement sauvegardé par `record`, à partir de son identifiant."""
        return Recording.load(self.directory / f"{recording_id}.json.gz")
