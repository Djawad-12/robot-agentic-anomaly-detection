"""Software and configuration faults provoked on URSim.

`URSimFaults` knows how to provoke each fault (`inject`) and how to bring the simulator back
to a working state (`revert`). The scenarios (`scenarios.yaml`) pair a fault with the answer
expected from the agent; the benchmarks use them to grade its diagnoses.

Usage:
    python -m tools.fault_injection.inject list
    python -m tools.fault_injection.inject <fault>           # inject
    python -m tools.fault_injection.inject <fault> --revert  # repair
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import threading
import time

from robot_agentic.config import load_settings, setup_logging
from robot_agentic.connectors.ursim import Dashboard, send_script

log = logging.getLogger(__name__)

FAULTS = {
    "wrong_payload": "Payload declared as 2 kg while the tool is empty: the torques no longer match.",
    "script_error": "Invalid URScript program sent to the controller.",
    "protective_stop": "Protective stop: the robot stops and refuses to move.",
    "controller_crash": "The controller process (URControl) is killed.",
    "freeze": "The whole controller is frozen for a few seconds (communication lost), then resumes.",
    "polyscope_freeze": "The PolyScope process (user interface, Dashboard server, pendant log) is frozen; the controller keeps running.",
    "network_cut": "The container is disconnected from the network: every interface becomes unreachable.",
    "cpu_starvation": "The container is limited to 0.1 CPU: every process, the real-time controller included, is starved.",
    "cpu_overload": "16 busy loops run inside the container, competing with the controller for the CPU.",
}

# Busy loop of the "cpu_overload" fault; its exact text is what `revert` looks for to stop it.
BURN = "while :; do :; done"


class URSimFaults:
    """Provokes and repairs the faults of `FAULTS` on a URSim running in Docker.

    Args:
        host: controller address.
        container: name of the URSim Docker container.
        freeze_s: duration of the "freeze" fault, in seconds.
    """

    def __init__(self, host: str, container: str, freeze_s: float = 8.0) -> None:
        """Remember the controller address and the container name."""
        self.host = host
        self.container = container
        self.freeze_s = freeze_s

    def inject(self, fault: str) -> None:
        """Provoke a fault and return at once (the freeze lifts by itself after `freeze_s`).

        Raises:
            ValueError: if the fault is unknown.
            subprocess.CalledProcessError: if a Docker command fails.
        """
        log.info("injecting fault %s", fault)
        match fault:
            case "wrong_payload":
                send_script(self.host, "set_target_payload(2.0, [0, 0, 0.05])")
            case "script_error":
                send_script(self.host, "movej(get_actual_joint_positions(), a=1.0, v=)")
            case "protective_stop":
                send_script(self.host, "protective_stop()")
            case "controller_crash":
                subprocess.run(["docker", "exec", self.container, "pkill", "-f", "URControl"], capture_output=True, timeout=30)
            case "freeze":
                subprocess.run(["docker", "pause", self.container], check=True, capture_output=True, timeout=30)
                threading.Timer(
                    self.freeze_s, lambda: subprocess.run(["docker", "unpause", self.container], capture_output=True, timeout=30)
                ).start()
            case "polyscope_freeze":
                # Frozen, not killed: on URSim, PolyScope ending stops the whole container.
                subprocess.run(["docker", "exec", self.container, "pkill", "-STOP", "-f", "felix.jar"], capture_output=True, timeout=30)
            case "network_cut":
                subprocess.run(["docker", "network", "disconnect", "bridge", self.container], check=True, capture_output=True, timeout=30)
            case "cpu_starvation":
                subprocess.run(["docker", "update", "--cpus", "0.1", self.container], check=True, capture_output=True, timeout=30)
            case "cpu_overload":
                loops = " ".join([f"(sh -c '{BURN}' &);"] * 16)
                subprocess.run(["docker", "exec", self.container, "sh", "-c", loops], check=True, capture_output=True, timeout=30)
            case _:
                raise ValueError(f"unknown fault: {fault}")

    def revert(self, fault: str) -> None:
        """Repair a fault and bring the robot back to RUNNING.

        A killed controller does not restart by itself (nothing supervises it in the container):
        the container is restarted, which takes about 20 seconds.

        Raises:
            ValueError: if the fault is unknown.
            TimeoutError: if the controller does not answer after the repair.
        """
        log.info("repairing fault %s", fault)
        match fault:
            case "wrong_payload":
                send_script(self.host, "set_target_payload(0.0, [0, 0, 0])")
            case "script_error" | "protective_stop":
                pass
            case "controller_crash":
                subprocess.run(["docker", "restart", self.container], check=True, capture_output=True, timeout=120)
            case "freeze":
                subprocess.run(["docker", "unpause", self.container], capture_output=True, timeout=30)
            case "polyscope_freeze":
                subprocess.run(["docker", "exec", self.container, "pkill", "-CONT", "-f", "felix.jar"], capture_output=True, timeout=30)
            case "network_cut":
                subprocess.run(["docker", "network", "connect", "bridge", self.container], capture_output=True, timeout=30)
            case "cpu_starvation":
                # `--cpus 0` does not remove a limit: give back every CPU of the host instead.
                subprocess.run(
                    ["docker", "update", "--cpus", str(os.cpu_count()), self.container], check=True, capture_output=True, timeout=30
                )
            case "cpu_overload":
                subprocess.run(["docker", "exec", self.container, "pkill", "-f", BURN], capture_output=True, timeout=30)
            case _:
                raise ValueError(f"unknown fault: {fault}")
        self.wait_ready()

    def wait_ready(self, timeout: float = 120.0) -> str:
        """Wait until the controller answers, then bring the robot back to RUNNING.

        After a container restart the Dashboard answers before the controller is ready (empty
        answer, then NO_CONTROLLER): powering on is retried until the robot is RUNNING.

        Returns:
            "RUNNING".

        Raises:
            TimeoutError: if the robot is not RUNNING in time.
        """
        deadline = time.time() + timeout
        mode = "no answer"
        while time.time() < deadline:
            try:
                mode = Dashboard(self.host, timeout=2).robot_mode()
                if mode not in ("", "NO_CONTROLLER", "BOOTING"):
                    mode = Dashboard(self.host).power_on_and_release(attempts=10)
                    if mode == "RUNNING":
                        return mode
            except OSError:
                pass
            time.sleep(2)
        raise TimeoutError(f"the robot is not RUNNING (last mode: {mode})")


def main() -> None:
    """Command line: list, inject or repair a fault."""
    parser = argparse.ArgumentParser(description="Faults provoked on URSim")
    parser.add_argument("fault", help="name of the fault, or 'list'")
    parser.add_argument("--revert", action="store_true", help="repair instead of injecting")
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings)
    if args.fault == "list":
        for name, description in FAULTS.items():
            print(f"{name:18} {description}")
        return
    faults = URSimFaults(settings.robot_host, settings.ursim_container)
    if args.revert:
        faults.revert(args.fault)
    else:
        faults.inject(args.fault)
    print(f"{args.fault}: {'repaired' if args.revert else 'injected'}")


if __name__ == "__main__":
    main()
