"""Le contrat que chaque machine doit respecter.

L'agent ne parle qu'à un `Connector`. Brancher une nouvelle machine (URSim, bras RoArm, pince
MotorScope…) revient à écrire une classe qui implémente ces méthodes : le reste du projet ne
change pas.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from .events import FaultCode, LogLine, Sample, ToolSpec


class Connector(ABC):
    """Accès aux quatre sources de données d'une machine, et aux actions possibles sur elle."""

    #: Nom court de la machine, repris dans les rapports et les fichiers de référence.
    name: str = "machine"

    # --- Les quatre flux ---------------------------------------------------------------

    @abstractmethod
    def signals(self) -> Iterator[Sample]:
        """Flux temps réel des mesures (positions, vitesses, courants, couples, températures)."""

    @abstractmethod
    def logs(self) -> Iterator[LogLine]:
        """Flux des nouvelles lignes du journal, à partir de maintenant."""

    @abstractmethod
    def faults(self) -> Iterator[FaultCode]:
        """Flux des codes d'erreur émis par le contrôleur, à partir de maintenant."""

    @abstractmethod
    def docs(self) -> list[str]:
        """Chemins des documents de la machine (manuels PDF, pages HTML enregistrées)."""

    # --- Actions -----------------------------------------------------------------------

    @abstractmethod
    def tools(self) -> list[ToolSpec]:
        """Actions propres à cette machine, proposées à l'agent."""

    @abstractmethod
    def call_tool(self, name: str, args: dict[str, Any]) -> str:
        """Exécute une action déjà validée par `core.safety`. Renvoie un compte rendu court."""

    @abstractmethod
    def stop(self) -> None:
        """Arrête immédiatement tout mouvement en cours."""

    # --- État --------------------------------------------------------------------------

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Vérifie que la machine répond. Renvoie {"ok": bool, ...détails}."""

    def snapshot(self) -> dict[str, Any]:
        """État courant résumé (mode, sécurité, programme…). Facultatif."""
        return {}

    def close(self) -> None:
        """Libère les connexions. Facultatif."""
