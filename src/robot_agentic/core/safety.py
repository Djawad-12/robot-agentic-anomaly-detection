"""Garde-fous : toute action demandée par l'agent passe par ici avant d'atteindre la machine.

Même si le LLM se trompe, la machine reste dans un cadre fixé à l'avance :
- les arguments numériques sont bornés par le connecteur (`ToolSpec.limits`) ; une valeur
  hors bornes est refusée et l'agent en reçoit la raison ;
- toute action qui met la machine en mouvement demande l'accord de l'opérateur, et son code
  PIN s'il est configuré.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .events import ToolSpec


class SafetyRefusal(Exception):
    """Action refusée par les garde-fous ; le message est renvoyé tel quel à l'agent."""


@dataclass
class SafetyPolicy:
    #: Fonction qui pose une question oui/non à l'opérateur (fournie par l'IHM).
    ask_operator: Callable[[str], bool]
    #: Code PIN demandé avant le premier mouvement d'une enquête (None : pas de PIN).
    pin: str | None = None
    ask_pin: Callable[[], str] | None = None
    #: Accord donné une fois pour toute l'enquête, ou à chaque mouvement.
    approve_once: bool = True
    _approved: bool = field(default=False, init=False)

    def reset(self) -> None:
        """À appeler au début de chaque enquête : l'accord ne se reporte pas d'une enquête à l'autre."""
        self._approved = False

    def check(self, spec: ToolSpec, args: dict[str, Any]) -> None:
        """Lève `SafetyRefusal` si l'action sort du cadre ou si l'opérateur refuse."""
        for key, (lo, hi) in spec.limits.items():
            if key not in args:
                continue
            try:
                value = float(args[key])
            except (TypeError, ValueError) as exc:
                raise SafetyRefusal(f"{spec.name} : {key} doit être un nombre") from exc
            if not lo <= value <= hi:
                raise SafetyRefusal(f"{spec.name} : {key}={value} hors des limites autorisées [{lo}, {hi}]")

        if not spec.moves_machine or (self.approve_once and self._approved):
            return
        if not self.ask_operator(f"L'agent veut lancer {spec.name}({_fmt(args)}). Autoriser ?"):
            raise SafetyRefusal("l'opérateur a refusé ce test ; propose une autre piste ou conclus avec les données disponibles")
        if self.pin and not self._approved:
            if self.ask_pin is None or self.ask_pin() != self.pin:
                raise SafetyRefusal("code PIN incorrect ; aucun mouvement n'a été effectué")
        self._approved = True


def _fmt(args: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in args.items())
