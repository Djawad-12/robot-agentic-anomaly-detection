"""Un connecteur par machine. `load` choisit le connecteur à partir de la configuration."""

from __future__ import annotations

from ..config import Settings
from ..core.interfaces import Connector


def load(settings: Settings) -> Connector:
    if settings.connector == "ursim":
        from .ursim import URSimConnector

        return URSimConnector(host=settings.robot_host, container=settings.ursim_container, manuals_dir=settings.manuals_dir / "ursim")
    if settings.connector == "fake":
        from .fake import FakeConnector

        return FakeConnector()
    if settings.connector in ("roarm", "motorscope"):
        raise NotImplementedError(f"le connecteur {settings.connector} arrive en phase 2")
    raise ValueError(f"connecteur inconnu : {settings.connector}")
