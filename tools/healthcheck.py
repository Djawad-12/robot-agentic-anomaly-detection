"""Vérifie en une commande que tout est prêt : machine, quatre flux, clé de l'API Claude.

Usage : python -m tools.healthcheck [--api]
  --api : vérifie aussi l'accès à l'API Claude (une requête de lecture, gratuite).
"""

from __future__ import annotations

import argparse
import sys
import time

import anthropic

from robot_agentic.config import load_settings, setup_logging
from robot_agentic.connectors import load
from robot_agentic.core.recorder import Recorder

OK, KO = "\033[32m✔\033[0m", "\033[31m✘\033[0m"


def main() -> int:
    """Lance les vérifications et affiche une ligne ✔ ou ✘ par point.

    Returns:
        0 si tout est prêt, 1 sinon.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", action="store_true", help="vérifie aussi l'accès à l'API Claude")
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings)
    connector = load(settings)
    results: list[tuple[bool, str]] = []

    health = connector.health()
    for key, value in health.items():
        if key != "ok":
            results.append((not str(value).startswith(("injoignable", "inaccessible", "fichier introuvable")), f"{key} : {value}"))

    recorder = Recorder(connector, settings.recordings_dir).start()
    time.sleep(3)
    rec = recorder.latest(2.5)
    results.append((rec.rate_hz > 10, f"flux de signaux : {rec.rate_hz:.0f} Hz, {len(rec.channels)} voies"))
    results.append(("logs" not in recorder.errors, f"flux du journal : {recorder.errors.get('logs', 'actif')}"))
    results.append(("faults" not in recorder.errors, f"flux des codes d'erreur : {recorder.errors.get('faults', 'actif')}"))

    if args.api:
        try:
            model = anthropic.Anthropic().models.retrieve(settings.model)
            results.append((True, f"API Claude : accès au modèle {model.id}"))
        except anthropic.AuthenticationError:
            results.append((False, "API Claude : clé absente ou invalide (ANTHROPIC_API_KEY ou `ant auth login`)"))
        except anthropic.APIConnectionError as exc:
            results.append((False, f"API Claude : injoignable ({exc})"))
        except anthropic.APIStatusError as exc:
            results.append((False, f"API Claude : erreur {exc.status_code}"))

    for ok, text in results:
        print(f"{OK if ok else KO} {text}")
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
