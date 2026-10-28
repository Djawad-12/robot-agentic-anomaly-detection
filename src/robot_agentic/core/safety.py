"""Safety checks: every action requested by the agent goes through here before reaching the machine.

Even if the LLM is wrong, the machine stays within limits fixed in advance:
- every argument is required and bounded by the connector (`ToolSpec.params`); a missing or
  out-of-range value is refused and the agent is told why;
- every action that moves the machine needs the operator's approval, each time, plus their
  PIN code (asked once per investigation) if one is configured.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .events import ToolSpec

log = logging.getLogger(__name__)


class SafetyRefusal(Exception):
    """Action refused by the safety checks; the message is sent back to the agent as is."""


@dataclass
class SafetyPolicy:
    """Decides whether an action may run.

    Attributes:
        ask_operator: asks the operator a yes/no question (provided by the terminal interface).
        pin: code asked before the first movement of an investigation; empty for none.
        ask_pin: asks the operator for the code.
    """

    ask_operator: Callable[[str], bool]
    pin: str = ""
    ask_pin: Callable[[], str] | None = None
    _pin_given: bool = field(default=False, init=False)

    def reset(self) -> None:
        """Forget that the PIN was given: call at the start of each investigation."""
        self._pin_given = False

    def check(self, spec: ToolSpec, args: dict[str, Any]) -> None:
        """Check the arguments, then get the operator's approval if the action moves the machine.

        Raises:
            SafetyRefusal: if an argument is missing or out of range, if the operator refuses,
                or if the PIN is wrong. Nothing has then been sent to the machine.
        """
        for key, p in spec.params.items():
            if key not in args:
                log.warning("refused %s: missing argument %s", spec.name, key)
                raise SafetyRefusal(f"{spec.name}: argument {key} is required")
            try:
                value = float(args[key])
            except (TypeError, ValueError) as exc:
                log.warning("refused %s: %s is not a number (%r)", spec.name, key, args[key])
                raise SafetyRefusal(f"{spec.name}: {key} must be a number") from exc
            if not p.low <= value <= p.high:
                log.warning("refused %s: %s=%s outside [%s, %s]", spec.name, key, value, p.low, p.high)
                raise SafetyRefusal(f"{spec.name}: {key}={value} outside the allowed range [{p.low}, {p.high}]")

        if not spec.moves_machine:
            return
        described = ", ".join(f"{k}={v}" for k, v in args.items())
        if not self.ask_operator(f"The agent wants to run {spec.name}({described}). Allow?"):
            log.warning("operator refused %s(%s)", spec.name, described)
            raise SafetyRefusal("the operator refused this test; try another lead or conclude with the data you have")
        if self.pin and not self._pin_given:
            if self.ask_pin is None or self.ask_pin() != self.pin:
                log.warning("refused %s: wrong PIN", spec.name)
                raise SafetyRefusal("wrong PIN; the machine did not move")
            self._pin_given = True
        log.info("movement allowed: %s(%s)", spec.name, described)
