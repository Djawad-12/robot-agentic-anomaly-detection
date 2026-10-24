"""Référence de bon fonctionnement.

Quand on sait que la machine va bien, on enregistre son comportement pendant un test type
(statistiques de chaque voie, et l'enregistrement complet). Les tests suivants lui sont
comparés : un écart net par rapport à cette référence est un indice de panne.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .recorder import Recording


@dataclass
class ChannelStats:
    mean: float
    std: float
    min: float
    max: float
    rms: float

    @classmethod
    def of(cls, x: np.ndarray) -> ChannelStats:
        x = x[np.isfinite(x)]
        if x.size == 0:
            return cls(*(float("nan"),) * 5)
        return cls(float(x.mean()), float(x.std()), float(x.min()), float(x.max()), float(np.sqrt(np.mean(x**2))))


@dataclass
class Baseline:
    """Référence pour un test donné (ex. "joint_test:2:+5") sur une machine."""

    machine: str
    test: str
    recording_id: str
    stats: dict[str, ChannelStats]

    @classmethod
    def from_recording(cls, rec: Recording, test: str) -> Baseline:
        return cls(rec.machine, test, rec.id, {k: ChannelStats.of(v) for k, v in rec.channels.items()})

    # --- Sauvegarde --------------------------------------------------------------------

    @staticmethod
    def path(directory: Path, machine: str, test: str) -> Path:
        return directory / machine / f"{test.replace(':', '_')}.json"

    def save(self, directory: Path) -> Path:
        path = self.path(directory, self.machine, self.test)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"machine": self.machine, "test": self.test, "recording_id": self.recording_id, "stats": {k: asdict(v) for k, v in self.stats.items()}}
        path.write_text(json.dumps(payload, indent=1))
        return path

    @classmethod
    def load(cls, directory: Path, machine: str, test: str) -> Baseline | None:
        path = cls.path(directory, machine, test)
        if not path.exists():
            return None
        p = json.loads(path.read_text())
        return cls(p["machine"], p["test"], p["recording_id"], {k: ChannelStats(**v) for k, v in p["stats"].items()})

    @staticmethod
    def available(directory: Path, machine: str) -> list[str]:
        folder = directory / machine
        return sorted(p.stem for p in folder.glob("*.json")) if folder.exists() else []
