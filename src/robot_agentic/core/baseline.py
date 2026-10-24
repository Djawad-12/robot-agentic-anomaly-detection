"""Référence de bon fonctionnement.

Quand on sait que la machine va bien, on enregistre son comportement pendant un test type :
les statistiques de chaque voie, et l'identifiant de l'enregistrement complet (pour tracer
la référence sur les graphiques). Les tests suivants lui sont comparés par `core.checks` :
un écart net par rapport à cette référence est un indice de panne.

Fichiers : `<baselines_dir>/<machine>/<test>.json`, un par test.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .recorder import Recording

log = logging.getLogger(__name__)


@dataclass
class ChannelStats:
    """Statistiques d'une voie sur un test. Valent NaN si la voie n'a aucune valeur finie."""

    mean: float
    std: float
    min: float
    max: float
    rms: float

    @classmethod
    def of(cls, x: np.ndarray) -> ChannelStats:
        """Calcule les statistiques d'une voie en ignorant les valeurs manquantes (NaN)."""
        x = x[np.isfinite(x)]
        if x.size == 0:
            nan = float("nan")
            return cls(nan, nan, nan, nan, nan)
        return cls(float(x.mean()), float(x.std()), float(x.min()), float(x.max()), float(np.sqrt(np.mean(x**2))))


@dataclass
class Baseline:
    """Référence d'un test sur une machine.

    `test` est la clé du test (ex. "joint_test:delta_deg=5:joint=1", voir
    `agent.tools.test_key`) : un test n'est comparé qu'à la référence du même test avec les
    mêmes arguments.
    """

    machine: str
    test: str
    recording_id: str
    stats: dict[str, ChannelStats]

    @classmethod
    def from_recording(cls, rec: Recording, test: str) -> Baseline:
        """Construit la référence à partir de l'enregistrement d'un test sur une machine saine."""
        return cls(rec.machine, test, rec.id, {k: ChannelStats.of(v) for k, v in rec.channels.items()})

    @staticmethod
    def path(directory: Path, machine: str, test: str) -> Path:
        """Chemin du fichier de la référence ; les « : » de la clé sont remplacés pour le système de fichiers."""
        return directory / machine / f"{test.replace(':', '_')}.json"

    def save(self, directory: Path) -> Path:
        """Écrit la référence (en remplaçant l'ancienne) et renvoie le chemin du fichier."""
        path = self.path(directory, self.machine, self.test)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "machine": self.machine,
            "test": self.test,
            "recording_id": self.recording_id,
            "stats": {k: asdict(v) for k, v in self.stats.items()},
        }
        path.write_text(json.dumps(payload, indent=1))
        log.info("référence enregistrée : %s / %s (%s)", self.machine, self.test, self.recording_id)
        return path

    @classmethod
    def load(cls, directory: Path, machine: str, test: str) -> Baseline | None:
        """Relit la référence d'un test ; None si elle n'a jamais été enregistrée."""
        path = cls.path(directory, machine, test)
        if not path.exists():
            return None
        p = json.loads(path.read_text())
        return cls(p["machine"], p["test"], p["recording_id"], {k: ChannelStats(**v) for k, v in p["stats"].items()})

    @staticmethod
    def available(directory: Path, machine: str) -> list[str]:
        """Noms des fichiers de référence d'une machine (forme fichier de la clé de test)."""
        folder = directory / machine
        return sorted(p.stem for p in folder.glob("*.json")) if folder.exists() else []
