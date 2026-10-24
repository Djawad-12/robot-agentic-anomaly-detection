"""Pannes logicielles et de configuration provoquées sur URSim.

`URSimFaults` sait provoquer chaque panne (`inject`) et remettre le simulateur en état de
marche (`revert`). Les scénarios (`scenarios.yaml`) associent une panne à la bonne réponse
attendue de l'agent ; les benchmarks s'en servent pour noter ses diagnostics.

Usage :
    python -m tools.fault_injection.inject list
    python -m tools.fault_injection.inject <panne>          # injecte
    python -m tools.fault_injection.inject <panne> --revert # répare
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import threading
import time

from robot_agentic.config import load_settings, setup_logging
from robot_agentic.connectors.ursim import urscript
from robot_agentic.connectors.ursim.dashboard import Dashboard

log = logging.getLogger(__name__)

FAULTS = {
    "wrong_payload": "Charge déclarée à 2 kg alors que l'outil est vide : les couples ne correspondent plus.",
    "script_error": "Programme URScript invalide envoyé au contrôleur.",
    "protective_stop": "Arrêt de protection : le robot s'arrête et refuse de bouger.",
    "controller_crash": "Arrêt brutal du processus du contrôleur (URControl).",
    "freeze": "Gel complet du contrôleur pendant quelques secondes (communication perdue), puis reprise.",
}


class URSimFaults:
    """Provoque et répare les pannes de `FAULTS` sur un URSim lancé dans Docker.

    Args:
        host: adresse du contrôleur.
        container: nom du conteneur Docker d'URSim.
        freeze_s: durée du gel pour la panne « freeze », en secondes.
    """

    def __init__(self, host: str | None = None, container: str | None = None, freeze_s: float = 8.0) -> None:
        """Lit l'adresse et le conteneur dans l'environnement s'ils ne sont pas donnés."""
        self.host = host or os.environ.get("ROBOT_HOST", "127.0.0.1")
        self.container = container or os.environ.get("URSIM_CONTAINER", "ursim")
        self.freeze_s = freeze_s

    def inject(self, fault: str) -> None:
        """Provoque une panne et rend la main aussitôt (le gel se lève tout seul après `freeze_s`).

        Raises:
            ValueError: si la panne est inconnue.
            subprocess.CalledProcessError: si une commande Docker échoue.
        """
        log.info("injection de la panne %s", fault)
        match fault:
            case "wrong_payload":
                urscript.send(self.host, "set_target_payload(2.0, [0, 0, 0.05])")
            case "script_error":
                urscript.send(self.host, "movej(get_actual_joint_positions(), a=1.0, v=)")
            case "protective_stop":
                urscript.send(self.host, "protective_stop()")
            case "controller_crash":
                subprocess.run(["docker", "exec", self.container, "pkill", "-f", "URControl"], capture_output=True, timeout=30)
            case "freeze":
                subprocess.run(["docker", "pause", self.container], check=True, capture_output=True, timeout=30)
                threading.Timer(
                    self.freeze_s, lambda: subprocess.run(["docker", "unpause", self.container], capture_output=True, timeout=30)
                ).start()
            case _:
                raise ValueError(f"panne inconnue : {fault}")

    def revert(self, fault: str) -> None:
        """Répare une panne et remet le robot en marche (RUNNING).

        Le contrôleur arrêté ne redémarre pas tout seul : on redémarre le conteneur, ce qui
        prend environ 20 secondes.

        Raises:
            ValueError: si la panne est inconnue.
            TimeoutError: si le contrôleur ne répond plus après la réparation.
        """
        log.info("réparation de la panne %s", fault)
        match fault:
            case "wrong_payload":
                urscript.send(self.host, "set_target_payload(0.0, [0, 0, 0])")
            case "script_error" | "protective_stop":
                pass
            case "controller_crash":
                subprocess.run(["docker", "restart", self.container], check=True, capture_output=True, timeout=120)
            case "freeze":
                subprocess.run(["docker", "unpause", self.container], capture_output=True, timeout=30)
            case _:
                raise ValueError(f"panne inconnue : {fault}")
        self.wait_ready()

    def wait_ready(self, timeout: float = 120.0) -> str:
        """Attend que le contrôleur réponde, puis remet le robot en marche.

        Returns:
            Le mode du robot (RUNNING si tout va bien).

        Raises:
            TimeoutError: si le contrôleur ne répond pas dans le délai.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                Dashboard(self.host, timeout=2).robot_mode()
            except OSError:
                time.sleep(2)
                continue
            # Le Dashboard répond avant que le contrôleur accepte la mise sous tension.
            time.sleep(3)
            return Dashboard(self.host).power_on_and_release()
        raise TimeoutError("le contrôleur ne répond pas")


def main() -> None:
    """Ligne de commande : liste, injecte ou répare une panne."""
    parser = argparse.ArgumentParser(description="Pannes provoquées sur URSim")
    parser.add_argument("fault", help="nom de la panne, ou « list »")
    parser.add_argument("--revert", action="store_true", help="répare au lieu d'injecter")
    args = parser.parse_args()
    setup_logging(load_settings())
    if args.fault == "list":
        for name, description in FAULTS.items():
            print(f"{name:18} {description}")
        return
    faults = URSimFaults()
    if args.revert:
        faults.revert(args.fault)
    else:
        faults.inject(args.fault)
    print(f"{args.fault} : {'réparé' if args.revert else 'injecté'}")


if __name__ == "__main__":
    main()
