"""Types de données communs à toutes les machines.

Chaque connecteur traduit ce que produit sa machine dans ces types : l'agent, les
vérifications et l'enregistreur ne connaissent qu'eux. Les instants `t` sont des secondes
depuis l'époque Unix (`time.time()`), prises à la réception sur ce PC.
"""

from __future__ import annotations

import time
from enum import StrEnum

from pydantic import BaseModel, Field


class Sample(BaseModel):
    """Un instant du flux de signaux : toutes les voies mesurées au même moment.

    `values` associe un nom de voie (ex. "joint_2_current_actual") à sa valeur. Regrouper les
    voies par instant garde l'enregistrement aligné dans le temps, sans rééchantillonnage.
    """

    t: float = Field(default_factory=time.time)
    values: dict[str, float]


class Level(StrEnum):
    """Gravité d'une ligne de journal ou d'un code d'erreur, de la plus faible à la plus forte."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"


class LogLine(BaseModel):
    """Une ligne du journal de la machine. `source` dit quel composant l'a écrite."""

    t: float = Field(default_factory=time.time)
    level: Level = Level.INFO
    source: str = ""
    text: str


class FaultCode(BaseModel):
    """Un code d'erreur émis par le contrôleur (ex. C204A3 sur un robot UR)."""

    t: float = Field(default_factory=time.time)
    code: str
    message: str = ""
    level: Level = Level.ERROR


class Severity(StrEnum):
    """Gravité d'une alerte levée par les vérifications déterministes."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Alert(BaseModel):
    """Anomalie détectée par les vérifications déterministes ; réveille l'agent en mode boucle.

    `evidence` contient les lignes de journal ou les codes d'erreur qui l'ont déclenchée,
    transmis tels quels à l'agent.
    """

    t: float = Field(default_factory=time.time)
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
