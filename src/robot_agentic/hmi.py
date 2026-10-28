"""Terminal interface: alerts, approval questions, the course of an investigation and the report.

This is the only module (with the command-line scripts) that writes with `print`. A web or
voice interface will only have to offer the same methods.
"""

from __future__ import annotations

import getpass
import json
import textwrap
from pathlib import Path
from typing import Any

from .core.events import Alert

DIM, BOLD, RED, GREEN, BLUE, RESET = "\033[2m", "\033[1m", "\033[31m", "\033[32m", "\033[34m", "\033[0m"


class CLI:
    """Terminal interface.

    Args:
        auto_approve: answers "yes" to every question (simulator and benchmarks only).
    """

    def __init__(self, auto_approve: bool = False) -> None:
        """Remember whether questions are answered automatically."""
        self.auto_approve = auto_approve

    def alert(self, alert: Alert) -> None:
        """Show an anomaly and its evidence, in red."""
        print(f"\n{RED}{BOLD}⚠ {alert.reason}{RESET}")
        for e in alert.evidence:
            print(f"  {e}")

    def confirm(self, question: str) -> bool:
        """Ask a yes/no question; only "y" and "yes" mean yes."""
        if self.auto_approve:
            print(f"{DIM}{question} → yes (automatic approval){RESET}")
            return True
        return input(f"{BOLD}{question} [y/N] {RESET}").strip().lower() in {"y", "yes"}

    def ask_pin(self) -> str:
        """Ask for the PIN code without showing it on screen."""
        return getpass.getpass("Operator PIN: ")

    def say(self, text: str) -> None:
        """Show a message."""
        print(f"\n{text}")

    def thinking(self, text: str) -> None:
        """Show the agent's reasoning summary, greyed and indented."""
        print(DIM + textwrap.indent(text.strip(), "  │ ") + RESET)

    def tool_call(self, name: str, args: dict[str, Any]) -> None:
        """Show a tool call in blue."""
        print(f"{BLUE}→ {name}({json.dumps(args, ensure_ascii=False)}){RESET}")

    def checklist(self, results: list[tuple[bool, str]]) -> None:
        """Show one ✔ or ✘ line per check."""
        for ok, text in results:
            print(f"{GREEN + '✔' if ok else RED + '✘'}{RESET} {text}")

    def report(self, report: dict[str, Any] | None, error: str | None, path: Path) -> None:
        """Show the final report: cause, evidence, repair, documentation."""
        print()
        if report is None:
            print(f"{RED}Investigation ended without a report: {error}{RESET}  ({path})")
            return
        print(f"{BOLD}Root cause{RESET} (confidence {report['confidence']}): {report['root_cause']}")
        for title, key in (("Evidence", "evidence"), ("Repair", "repair"), ("Documentation", "references")):
            if report.get(key):
                print(f"\n{BOLD}{title}{RESET}")
                for i, item in enumerate(report[key], 1):
                    print(f"  {i}. {item}")
        print(f"\n{DIM}Full report: {path}{RESET}")
