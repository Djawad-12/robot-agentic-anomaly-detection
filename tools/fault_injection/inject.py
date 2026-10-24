"""Pannes logicielles et de configuration provoquées sur URSim.

Chaque panne a une fonction `inject` et une fonction `revert` qui remet le simulateur en
état de marche. Les scénarios (`scenarios.yaml`) associent une panne à la bonne réponse
attendue de l'agent ; les benchmarks s'en servent pour noter ses diagnostics.

Usage :
    python -m tools.fault_injection.inject list
    python -m tools.fault_injection.inject <panne>          # injecte
    python -m tools.fault_injection.inject <panne> --revert # répare
"""

from __future__ import annotations

import argparse
import os
import subprocess
import threading
import time

from robot_agentic.connectors.ursim import urscript
from robot_agentic.connectors.ursim.dashboard import Dashboard

HOST = os.environ.get("ROBOT_HOST", "127.0.0.1")
CONTAINER = os.environ.get("URSIM_CONTAINER", "ursim")


def _docker(*args: str) -> None:
    subprocess.run(["docker", *args], check=True, capture_output=True, timeout=60)


def wait_ready(timeout: float = 120.0) -> str:
    """Attend que le contrôleur réponde, puis remet le robot en marche."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            Dashboard(HOST, timeout=2).robot_mode()
            time.sleep(3)
            return Dashboard(HOST).power_on_and_release()
        except OSError:
            time.sleep(2)
    raise TimeoutError("le contrôleur ne répond pas")


# --- Pannes ------------------------------------------------------------------------------

def wrong_payload_inject() -> None:
    """Charge déclarée à 2 kg alors que l'outil est vide : les couples ne correspondent plus."""
    urscript.send(HOST, "set_target_payload(2.0, [0, 0, 0.05])")


def wrong_payload_revert() -> None:
    urscript.send(HOST, "set_target_payload(0.0, [0, 0, 0])")


def script_error_inject() -> None:
    """Programme URScript invalide envoyé au contrôleur."""
    urscript.send(HOST, "movej(get_actual_joint_positions(), a=1.0, v=)")


def script_error_revert() -> None:
    pass


def protective_stop_inject() -> None:
    """Arrêt de protection : le robot s'arrête et refuse de bouger."""
    urscript.send(HOST, "protective_stop()")


def protective_stop_revert() -> None:
    Dashboard(HOST).power_on_and_release()


def controller_crash_inject() -> None:
    """Arrêt brutal du processus du contrôleur (URControl)."""
    subprocess.run(["docker", "exec", CONTAINER, "pkill", "-f", "URControl"], capture_output=True, timeout=30)


def controller_crash_revert() -> None:
    _docker("restart", CONTAINER)
    wait_ready()


def freeze_inject(seconds: float = 8.0) -> None:
    """Gel complet du contrôleur pendant quelques secondes (communication perdue), puis reprise."""
    _docker("pause", CONTAINER)
    threading.Timer(seconds, lambda: _docker("unpause", CONTAINER)).start()


def freeze_revert() -> None:
    subprocess.run(["docker", "unpause", CONTAINER], capture_output=True, timeout=30)


FAULTS = {
    "wrong_payload": (wrong_payload_inject, wrong_payload_revert),
    "script_error": (script_error_inject, script_error_revert),
    "protective_stop": (protective_stop_inject, protective_stop_revert),
    "controller_crash": (controller_crash_inject, controller_crash_revert),
    "freeze": (freeze_inject, freeze_revert),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Pannes provoquées sur URSim")
    parser.add_argument("fault", help="nom de la panne, ou « list »")
    parser.add_argument("--revert", action="store_true", help="répare au lieu d'injecter")
    args = parser.parse_args()
    if args.fault == "list":
        for name, (inject, _) in FAULTS.items():
            print(f"{name:18} {inject.__doc__.strip().splitlines()[0]}")
        return
    inject, revert = FAULTS[args.fault]
    (revert if args.revert else inject)()
    print(f"{args.fault} : {'réparé' if args.revert else 'injecté'}")


if __name__ == "__main__":
    main()
