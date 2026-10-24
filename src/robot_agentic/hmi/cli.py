"""Interface en terminal : alertes, demandes d'accord, déroulé de l'enquête et rapport.

Implémente le protocole `hmi.HMI`. C'est le seul module (avec les scripts en ligne de
commande) qui écrit avec `print`.
"""

from __future__ import annotations

import getpass
import json
import textwrap
from pathlib import Path
from typing import Any

from ..core.events import Alert

DIM, BOLD, RED, BLUE, RESET = "\033[2m", "\033[1m", "\033[31m", "\033[34m", "\033[0m"


class CLI:
    """Interface terminal.

    Args:
        auto_approve: répond « oui » à toutes les questions (simulateur et benchmarks uniquement).
        show_thinking: affiche le résumé de la réflexion de l'agent.
    """

    def __init__(self, auto_approve: bool = False, show_thinking: bool = True) -> None:
        """Mémorise les options d'affichage."""
        self.auto_approve = auto_approve
        self.show_thinking = show_thinking

    def alert(self, alert: Alert) -> None:
        """Affiche une anomalie et ses indices, en rouge."""
        print(f"\n{RED}{BOLD}⚠ {alert.reason}{RESET}")
        for e in alert.evidence:
            print(f"  {e}")

    def confirm(self, question: str) -> bool:
        """Pose une question oui/non ; seuls « o », « oui », « y » et « yes » valent oui."""
        if self.auto_approve:
            print(f"{DIM}{question} → oui (accord automatique){RESET}")
            return True
        return input(f"{BOLD}{question} [o/N] {RESET}").strip().lower() in {"o", "oui", "y", "yes"}

    def ask_pin(self) -> str:
        """Demande le code PIN sans l'afficher à l'écran."""
        return getpass.getpass("Code PIN de l'opérateur : ")

    def say(self, text: str) -> None:
        """Affiche un message."""
        print(f"\n{text}")

    def thinking(self, text: str) -> None:
        """Affiche la réflexion de l'agent en grisé, indentée."""
        if self.show_thinking:
            print(DIM + textwrap.indent(text.strip(), "  │ ") + RESET)

    def tool_call(self, name: str, args: dict[str, Any]) -> None:
        """Affiche un appel d'outil en bleu."""
        print(f"{BLUE}→ {name}({json.dumps(args, ensure_ascii=False)}){RESET}")

    def report(self, report: dict[str, Any] | None, error: str | None, path: Path) -> None:
        """Affiche le rapport final : cause, éléments, réparation, documentation."""
        print()
        if report is None:
            print(f"{RED}Enquête terminée sans rapport : {error}{RESET}  ({path})")
            return
        print(f"{BOLD}Cause racine{RESET} (confiance {report['confidence']}) : {report['root_cause']}")
        for title, key in (("Éléments", "evidence"), ("Réparation", "repair"), ("Documentation", "references")):
            if report.get(key):
                print(f"\n{BOLD}{title}{RESET}")
                for i, item in enumerate(report[key], 1):
                    print(f"  {i}. {item}")
        print(f"\n{DIM}Rapport complet : {path}{RESET}")

    def info(self, payload: Any) -> None:
        """Affiche une donnée structurée en JSON indenté."""
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
