"""Configuration lue dans l'environnement (et le fichier .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    connector: str = "ursim"
    robot_host: str = "127.0.0.1"
    ursim_container: str = "ursim"
    model: str = "claude-opus-5-5"
    recording_token_budget: int = 40_000
    operator_pin: str | None = None
    data_dir: Path = Path("data")
    manuals_dir: Path = Path("manuals")
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def baselines_dir(self) -> Path:
        return self.data_dir / "baselines"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def llm_log(self) -> Path:
        return self.data_dir / "llm_calls.jsonl"


def load_settings() -> Settings:
    load_dotenv()
    env = os.environ
    return Settings(
        connector=env.get("CONNECTOR", "ursim"),
        robot_host=env.get("ROBOT_HOST", "127.0.0.1"),
        ursim_container=env.get("URSIM_CONTAINER", "ursim"),
        model=env.get("CLAUDE_MODEL", "claude-opus-5-5"),
        recording_token_budget=int(env.get("RECORDING_TOKEN_BUDGET", "40000")),
        operator_pin=env.get("OPERATOR_PIN") or None,
        data_dir=Path(env.get("DATA_DIR", "data")),
        manuals_dir=Path(env.get("MANUALS_DIR", "manuals")),
    )
