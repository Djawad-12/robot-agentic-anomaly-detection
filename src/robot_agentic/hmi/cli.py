"""Interface en terminal : alertes, demandes d'accord, déroulé de l'enquête et rapport.

Toute autre interface (web plus tard) n'a qu'à fournir les mêmes méthodes.
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
    def __init__(self, auto_approve: bool = False, show_thinking: bool = True):
        self.auto_approve = auto_approve
        self.show_thinking = show_thinking

    def alert(self, alert: Alert) -> None:
        print(f"\n{RED}{BOLD}⚠ {alert.reason}{RESET}")
        for e in alert.evidence:
            print(f"  {e}")

    def confirm(self, question: str) -> bool:
        if self.auto_approve:
            print(f"{DIM}{question} → oui (accord automatique){RESET}")
            return True
        return input(f"{BOLD}{question} [o/N] {RESET}").strip().lower() in {"o", "oui", "y", "yes"}

    def ask_pin(self) -> str:
        return getpass.getpass("Code PIN de l'opérateur : ")

    def say(self, text: str) -> None:
        print(f"\n{text}")

    def thinking(self, text: str) -> None:
        if self.show_thinking:
            print(DIM + textwrap.indent(text.strip(), "  │ ") + RESET)

    def tool_call(self, name: str, args: dict) -> None:
        print(f"{BLUE}→ {name}({json.dumps(args, ensure_ascii=False)}){RESET}")

    def report(self, report: dict | None, error: str | None, path: Path) -> None:
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
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
