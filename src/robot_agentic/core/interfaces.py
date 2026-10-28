"""The contract every machine must respect.

The agent only talks to a `Connector`. Plugging in a new machine means writing a class that
implements these methods: the rest of the project does not change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from .events import FaultCode, LogLine, Sample, ToolSpec

if TYPE_CHECKING:
    from .recorder import Recording


class Connector(ABC):
    """Access to the four data sources of a machine, and to the actions possible on it.

    The three streams (`signals`, `logs`, `faults`) are blocking iterators, each read in its
    own thread by `core.recorder.Recorder`. A stream that loses its source raises an exception
    (usually ConnectionError): the `Recorder` restarts it.
    """

    #: Short machine name, used in reports and recording files.
    name: str = "machine"

    @abstractmethod
    def signals(self) -> Iterator[Sample]:
        """Live stream of measurements (positions, speeds, currents, torques, temperatures)."""

    @abstractmethod
    def logs(self) -> Iterator[LogLine]:
        """Stream of new log lines, from now on."""

    @abstractmethod
    def faults(self) -> Iterator[FaultCode]:
        """Stream of new fault codes raised by the controller, from now on."""

    @abstractmethod
    def docs(self) -> list[str]:
        """Paths of the machine's documents (PDF manuals, saved HTML pages)."""

    @abstractmethod
    def tools(self) -> list[ToolSpec]:
        """Actions specific to this machine, offered to the agent."""

    @abstractmethod
    def call_tool(self, name: str, args: dict[str, Any]) -> str | Recording:
        """Run an action already validated by `core.safety`.

        Returns:
            A short summary, or a `Recording` for an action that reads data kept by the
            machine itself (e.g. a black box): the agent then receives it like a test.
        """

    @abstractmethod
    def stop(self) -> None:
        """Stop any movement in progress immediately."""

    @abstractmethod
    def health(self) -> list[tuple[bool, str]]:
        """Check that the machine answers: one (ok, description) pair per check."""

    def snapshot(self) -> dict[str, Any]:
        """Current state in short (mode, safety, program…). Optional."""
        return {}
