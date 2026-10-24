"""Configuration lue dans l'environnement (et le fichier .env), et mise en place des journaux."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    """Réglages d'une exécution, lus par `load_settings`.

    Les dossiers de données dérivent tous de `data_dir`, pour qu'un test ou une démonstration
    puisse travailler dans un dossier à part sans toucher aux données réelles.
    """

    connector: str = "ursim"
    robot_host: str = "127.0.0.1"
    ursim_container: str = "ursim"
    model: str = "claude-opus-5-5"
    recording_token_budget: int = 40_000
    operator_pin: str | None = None
    log_level: str = "WARNING"
    data_dir: Path = Path("data")
    manuals_dir: Path = Path("manuals")

    @property
    def recordings_dir(self) -> Path:
        """Dossier des enregistrements de tests (un fichier .json.gz par enregistrement)."""
        return self.data_dir / "recordings"

    @property
    def baselines_dir(self) -> Path:
        """Dossier des références de bon fonctionnement, rangées par machine."""
        return self.data_dir / "baselines"

    @property
    def reports_dir(self) -> Path:
        """Dossier des rapports d'enquête."""
        return self.data_dir / "reports"

    @property
    def llm_log(self) -> Path:
        """Journal des appels à Claude (une ligne JSON par appel : tokens, coût, durée)."""
        return self.data_dir / "llm_calls.jsonl"

    @property
    def log_file(self) -> Path:
        """Journal complet de l'application, au niveau DEBUG."""
        return self.data_dir / "robot_agentic.log"


def load_settings() -> Settings:
    """Lit les réglages dans les variables d'environnement, après avoir chargé le fichier .env.

    Returns:
        Les réglages ; une variable absente garde sa valeur par défaut.
    """
    load_dotenv()
    env = os.environ
    return Settings(
        connector=env.get("CONNECTOR", "ursim"),
        robot_host=env.get("ROBOT_HOST", "127.0.0.1"),
        ursim_container=env.get("URSIM_CONTAINER", "ursim"),
        model=env.get("CLAUDE_MODEL", "claude-opus-5-5"),
        recording_token_budget=int(env.get("RECORDING_TOKEN_BUDGET", "40000")),
        operator_pin=env.get("OPERATOR_PIN") or None,
        log_level=env.get("LOG_LEVEL", "WARNING").upper(),
        data_dir=Path(env.get("DATA_DIR", "data")),
        manuals_dir=Path(env.get("MANUALS_DIR", "manuals")),
    )


def setup_logging(settings: Settings) -> None:
    """Envoie les journaux vers un fichier (dès DEBUG) et vers le terminal (dès `log_level`).

    Le fichier est `settings.log_file`. Le terminal n'affiche par défaut qu'à partir de
    WARNING, pour ne pas noyer l'interface.

    Les bibliothèques bavardes (requêtes HTTP, matplotlib) sont limitées à WARNING.
    """
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s [%(threadName)s] %(message)s"))
    console = logging.StreamHandler()
    console.setLevel(settings.log_level)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s : %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers[:] = [file_handler, console]
    for noisy in ("httpx", "httpcore", "anthropic", "matplotlib", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
