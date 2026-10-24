"""Interface avec l'opérateur.

`HMI` décrit ce que l'agent attend d'une interface. `cli.CLI` l'implémente dans le
terminal ; une interface web n'aura qu'à fournir les mêmes méthodes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..core.events import Alert


class HMI(Protocol):
    """Méthodes appelées par le graphe et les garde-fous pendant une enquête."""

    def alert(self, alert: Alert) -> None:
        """Signale une anomalie détectée en surveillance."""
        ...

    def confirm(self, question: str) -> bool:
        """Pose une question oui/non à l'opérateur et renvoie sa réponse."""
        ...

    def ask_pin(self) -> str:
        """Demande le code PIN de l'opérateur."""
        ...

    def say(self, text: str) -> None:
        """Affiche un message (texte de l'agent ou information)."""
        ...

    def thinking(self, text: str) -> None:
        """Affiche le résumé de la réflexion de l'agent."""
        ...

    def tool_call(self, name: str, args: dict[str, Any]) -> None:
        """Affiche un appel d'outil de l'agent."""
        ...

    def report(self, report: dict[str, Any] | None, error: str | None, path: Path) -> None:
        """Affiche le rapport final, ou la raison de son absence."""
        ...
