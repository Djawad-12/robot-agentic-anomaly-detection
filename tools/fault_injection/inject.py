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
from pathlib import Path

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
    "network_delay": "Every network packet of the container is delayed by 300 ms (± 50 ms).",
    "packet_loss": "20 % of the container's network packets are lost.",
    "port_blocked": "Port 30003 (Real-Time interface, 500 Hz signals) is blocked; the other ports keep working.",
    "disk_full": "The disk holding URSim's files (logs, programs, black boxes) is filled up.",
}

# Busy loop of the "cpu_overload" fault; its exact text is what `revert` looks for to stop it.
BURN = "while :; do :; done"

# Network faults run tc / iptables from a small helper image that joins URSim's network
# namespace: the URSim image has neither tool, nor the right to change its own network.
NETEM_IMAGE = "robot-agentic-netem"
NETEM_DOCKERFILE = Path(__file__).parent / "netem"
NETWORK_FAULTS = {
    "network_delay": ("tc qdisc add dev eth0 root netem delay 300ms 50ms", "tc qdisc del dev eth0 root"),
    "packet_loss": ("tc qdisc add dev eth0 root netem loss 20%", "tc qdisc del dev eth0 root"),
    "port_blocked": ("iptables -A INPUT -p tcp --dport 30003 -j DROP", "iptables -D INPUT -p tcp --dport 30003 -j DROP"),
}

# File that fills the disk of the "disk_full" fault (URSim must run on its own small disk:
# see README.md, otherwise this would fill the computer's disk).
FILL_FILE = "/ursim/.robot_agentic_fill"


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
            RuntimeError: for "disk_full", if URSim does not run on its own small disk.
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
            case "network_delay" | "packet_loss" | "port_blocked":
                self._network(NETWORK_FAULTS[fault][0])
            case "disk_full":
                size = subprocess.run(
                    ["docker", "exec", self.container, "df", "-B1", "--output=size", "/ursim"], capture_output=True, text=True
                )
                if int(size.stdout.split()[-1]) > 2 * 1024**3:
                    raise RuntimeError("URSim's disk is larger than 2 GB: it is probably the computer's disk, refusing to fill it")
                # dd stops with an error when the disk is full: that is the goal, not a failure. Large
                # blocks first (fast), then 4 kB blocks for the last megabyte that 1 MB blocks leave.
                for i, block in enumerate(("1M", "4k")):
                    cmd = ["docker", "exec", self.container, "dd", "if=/dev/zero", f"of={FILL_FILE}{i}", f"bs={block}"]
                    subprocess.run(cmd, capture_output=True, timeout=120)
            case _:
                raise ValueError(f"unknown fault: {fault}")

    def _network(self, command: str) -> None:
        """Run a tc or iptables command in URSim's network namespace, building the helper image if needed.

        Raises:
            subprocess.CalledProcessError: if the command fails.
        """
        if subprocess.run(["docker", "image", "inspect", NETEM_IMAGE], capture_output=True).returncode != 0:
            subprocess.run(
                ["docker", "build", "-q", "-t", NETEM_IMAGE, str(NETEM_DOCKERFILE)], check=True, capture_output=True, timeout=300
            )
        net = f"container:{self.container}"
        subprocess.run(
            ["docker", "run", "--rm", "--net", net, "--cap-add", "NET_ADMIN", NETEM_IMAGE, "sh", "-c", command],
            check=True,
            capture_output=True,
            timeout=60,
        )

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
            case "network_delay" | "packet_loss" | "port_blocked":
                self._network(NETWORK_FAULTS[fault][1])
            case "disk_full":
                subprocess.run(
                    ["docker", "exec", self.container, "sh", "-c", f"rm -f {FILL_FILE}*"], check=True, capture_output=True, timeout=30
                )
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
