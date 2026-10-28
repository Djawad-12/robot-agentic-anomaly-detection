"""Settings read from the environment (and the .env file), and logging setup."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    """Settings of one run, read by `load_settings`.

    Each field is read from the environment variable of the same name in upper case
    (`robot_host` ← `ROBOT_HOST`); a missing variable keeps the default below. Every data
    folder derives from `data_dir`, so that a test can work in a separate folder without
    touching the real data.
    """

    connector: str = "ursim"
    robot_host: str = "127.0.0.1"
    ursim_container: str = "ursim"
    claude_model: str = "claude-opus-5-5"
    recording_token_budget: int = 40_000
    operator_pin: str = ""
    log_level: str = "WARNING"
    data_dir: Path = Path("data")
    manuals_dir: Path = Path("manuals")

    @property
    def recordings_dir(self) -> Path:
        """Folder of the test recordings (one .json.gz file per recording)."""
        return self.data_dir / "recordings"

    @property
    def reports_dir(self) -> Path:
        """Folder of the investigation reports."""
        return self.data_dir / "reports"

    @property
    def llm_log(self) -> Path:
        """Log of the calls to Claude (one JSON line per call: tokens, cost, duration)."""
        return self.data_dir / "llm_calls.jsonl"

    @property
    def log_file(self) -> Path:
        """Full application log, at DEBUG level."""
        return self.data_dir / "robot_agentic.log"


def load_settings() -> Settings:
    """Read the settings from the environment variables, after loading the .env file."""
    load_dotenv()
    values = {}
    for f in fields(Settings):
        raw = os.environ.get(f.name.upper())
        if raw:
            values[f.name] = type(f.default)(raw)
    return Settings(**values)


def setup_logging(settings: Settings) -> None:
    """Send the logs to a file (from DEBUG) and to the terminal (from `log_level`).

    The terminal only shows WARNING and above by default, so as not to drown the interface.
    Talkative libraries (HTTP requests, matplotlib) are limited to WARNING.
    """
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s [%(threadName)s] %(message)s"))
    console = logging.StreamHandler()
    console.setLevel(settings.log_level.upper())
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers[:] = [file_handler, console]
    for noisy in ("httpx", "httpcore", "anthropic", "matplotlib", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
