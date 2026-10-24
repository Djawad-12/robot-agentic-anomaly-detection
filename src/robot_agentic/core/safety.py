"""Garde-fous : toute action demandée par l'agent passe par ici avant d'atteindre la machine.

Même si le LLM se trompe, la machine reste dans un cadre fixé à l'avance :
- les arguments numériques sont bornés par le connecteur (`ToolSpec.limits`) ; une valeur
  hors bornes est refusée et l'agent en reçoit la raison ;
- toute action qui met la machine en mouvement demande l'accord de l'opérateur, et son code
  PIN s'il est configuré.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .events import ToolSpec

log = logging.getLogger(__name__)


class SafetyRefusal(Exception):
    """Action refusée par les garde-fous ; le message est renvoyé tel quel à l'agent."""


@dataclass
class SafetyPolicy:
    """Décide si une action peut être exécutée.

    Attributes:
        ask_operator: pose une question oui/non à l'opérateur (fournie par l'IHM).
        pin: code demandé avant le premier mouvement d'une enquête ; None pour s'en passer.
        ask_pin: demande le code à l'opérateur.
        approve_once: True : un accord vaut pour toute l'enquête ; False : un accord par mouvement.
    """

    ask_operator: Callable[[str], bool]
    pin: str | None = None
    ask_pin: Callable[[], str] | None = None
    approve_once: bool = True
    _approved: bool = field(default=False, init=False)

    def reset(self) -> None:
        """Oublie l'accord donné : à appeler au début de chaque enquête."""
        self._approved = False

    def check(self, spec: ToolSpec, args: dict[str, Any]) -> None:
        """Vérifie les bornes, puis obtient l'accord de l'opérateur si l'action bouge la machine.

        Raises:
            SafetyRefusal: si un argument sort de ses bornes, si l'opérateur refuse ou si le
                code PIN est faux. Rien n'a alors été envoyé à la machine.
        """
        for key, (lo, hi) in spec.limits.items():
            if key not in args:
                continue
            try:
                value = float(args[key])
            except (TypeError, ValueError) as exc:
                log.warning("refus de %s : %s n'est pas un nombre (%r)", spec.name, key, args[key])
                raise SafetyRefusal(f"{spec.name} : {key} doit être un nombre") from exc
            if not lo <= value <= hi:
                log.warning("refus de %s : %s=%s hors de [%s, %s]", spec.name, key, value, lo, hi)
                raise SafetyRefusal(f"{spec.name} : {key}={value} hors des limites autorisées [{lo}, {hi}]")

        if not spec.moves_machine or (self.approve_once and self._approved):
            return
        described = ", ".join(f"{k}={v}" for k, v in args.items())
        if not self.ask_operator(f"L'agent veut lancer {spec.name}({described}). Autoriser ?"):
            log.warning("refus de %s(%s) par l'opérateur", spec.name, described)
            raise SafetyRefusal("l'opérateur a refusé ce test ; propose une autre piste ou conclus avec les données disponibles")
        if self.pin and not self._approved and (self.ask_pin is None or self.ask_pin() != self.pin):
            log.warning("refus de %s : code PIN incorrect", spec.name)
            raise SafetyRefusal("code PIN incorrect ; aucun mouvement n'a été effectué")
        self._approved = True
        log.info("mouvement autorisé : %s(%s)", spec.name, described)
