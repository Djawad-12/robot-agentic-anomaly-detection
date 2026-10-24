"""Types de données communs à toutes les machines.

Chaque connecteur traduit ce que produit sa machine dans ces types : l'agent, les
vérifications et l'enregistreur ne connaissent qu'eux.
"""

from __future__ import annotations

import time
from enum import Enum

from pydantic import BaseModel, Field


def now() -> float:
    return time.time()


class Sample(BaseModel):
    """Un instant du flux de signaux : toutes les voies mesurées au même moment.

    `values` associe un nom de voie (ex. "joint_2_current") à sa valeur. Regrouper les voies
    par instant garde l'enregistrement aligné dans le temps, sans rééchantillonnage.
    """

    t: float = Field(default_factory=now)
    values: dict[str, float]


class Level(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"


class LogLine(BaseModel):
    """Une ligne du journal de la machine."""

    t: float = Field(default_factory=now)
    level: Level = Level.INFO
    source: str = ""
    text: str


class FaultCode(BaseModel):
    """Un code d'erreur émis par le contrôleur (ex. C204A3 sur un robot UR)."""

    t: float = Field(default_factory=now)
    code: str
    message: str = ""
    level: Level = Level.ERROR


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Alert(BaseModel):
    """Anomalie détectée par les vérifications déterministes ; réveille l'agent en mode boucle."""

    t: float = Field(default_factory=now)
    reason: str
    severity: Severity = Severity.WARNING
    evidence: list[str] = Field(default_factory=list)


class ToolSpec(BaseModel):
    """Action qu'un connecteur met à disposition de l'agent (ex. bouger la main du robot).

    `input_schema` est un schéma JSON. `moves_machine` indique que l'action met la machine en
    mouvement : elle passe alors par les garde-fous et l'accord de l'opérateur, et elle est
    enregistrée. `limits` borne les arguments numériques ({"delta_deg": (-5, 5)}) ; une valeur
    hors bornes est refusée par `core.safety`, jamais corrigée en silence.
    """

    name: str
    description: str
    input_schema: dict
    moves_machine: bool = False
    limits: dict[str, tuple[float, float]] = Field(default_factory=dict)
