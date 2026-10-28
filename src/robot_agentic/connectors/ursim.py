"""URSim connector: the official Universal Robots simulator (UR3e), running in Docker.

This file is the only place that knows the UR controller interfaces:
- signals: Real-Time interface (port 30003, one binary packet every 2 ms = 500 Hz);
- fault codes: Primary interface (port 30001, "Robot Message" packets);
- log: controller files inside the container (`docker exec … tail -F`), as one would do over
  SSH on a real controller;
- actions: URScript programs (port 30002); state and recovery via the Dashboard (port 29999);
- documentation: manuals placed in `manuals/ursim/`.
Formats: Universal Robots "Remote Control Via TCP/IP" and "Client Interface" documents.

Every action that moves the robot stays small and slow; its bounds are declared in its
`ToolSpec` and enforced by `core.safety` before anything is sent.
"""

from __future__ import annotations

import logging
import math
import re
import socket
import struct
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..core.events import FaultCode, Level, LogLine, Param, Sample, ToolSpec
from ..core.interfaces import Connector

log = logging.getLogger(__name__)

REALTIME_PORT, PRIMARY_PORT, URSCRIPT_PORT, DASHBOARD_PORT = 30003, 30001, 30002, 29999

# 10 % of the slowest UR3e joint speed (π rad/s for the wrists).
JOINT_SPEED = 0.1 * math.pi  # rad/s
JOINT_ACC = 0.5  # rad/s²
TOOL_SPEED = 0.025  # m/s
TOOL_ACC = 0.25  # m/s²

JOINT_NAMES = ["base", "shoulder", "elbow", "wrist 1", "wrist 2", "wrist 3"]

DOC_SUFFIXES = {".pdf", ".html", ".htm", ".txt", ".md"}

# Real-Time packet: field → (byte offset from the start of the packet, size included; number
# of doubles; channel names). UR units: rad, rad/s, A, Nm, m, N, °C, V.
_JOINTS = range(6)
_TCP = ["x", "y", "z", "rx", "ry", "rz"]
REALTIME_FIELDS: dict[str, tuple[int, int, list[str]]] = {
    "q_target": (12, 6, [f"joint_{i}_q_target" for i in _JOINTS]),
    "torque_target": (204, 6, [f"joint_{i}_torque_target" for i in _JOINTS]),
    "q_actual": (252, 6, [f"joint_{i}_q_actual" for i in _JOINTS]),
    "qd_actual": (300, 6, [f"joint_{i}_qd_actual" for i in _JOINTS]),
    "current_actual": (348, 6, [f"joint_{i}_current_actual" for i in _JOINTS]),
    "tcp_pose": (444, 6, [f"tcp_pose_{a}" for a in _TCP]),
    "tcp_force": (540, 6, [f"tcp_force_{a}" for a in _TCP]),
    "motor_temperature": (692, 6, [f"joint_{i}_motor_temperature" for i in _JOINTS]),
    "robot_mode": (756, 1, ["robot_mode"]),
    "safety_mode": (812, 1, ["safety_mode"]),
    "tool_accelerometer": (868, 3, ["tool_acc_x", "tool_acc_y", "tool_acc_z"]),
    "speed_scaling": (940, 1, ["speed_scaling"]),
    "main_voltage": (972, 1, ["main_voltage"]),
    "robot_voltage": (980, 1, ["robot_voltage"]),
    "robot_current": (988, 1, ["robot_current"]),
    "joint_voltage": (996, 6, [f"joint_{i}_voltage" for i in _JOINTS]),
}

# Primary interface: packet type and "Robot Message" types.
ROBOT_MESSAGE = 20
ERROR_CODE, KEY, RUNTIME_EXCEPTION = 6, 7, 10
REPORT_LEVELS = {0: Level.DEBUG, 1: Level.INFO, 2: Level.WARNING, 3: Level.WARNING, 4: Level.ERROR, 5: Level.FATAL}
MESSAGE_SOURCES = {
    -2: "UNDEFINED", -1: "UNKNOWN", 0: "SAFETY", 1: "CONTROLLER", 2: "RTMACHINE", 3: "SIMULATED", 4: "POLYSCOPE",
    5: "PROCESSOR", 8: "CONTROLBOX", 110: "JOINT0", 111: "JOINT1", 112: "JOINT2", 113: "JOINT3", 114: "JOINT4",
    115: "JOINT5", 120: "TOOL",
}  # fmt: skip

# Controller log files inside the container.
LOG_FILES = {"/ursim/URControl.log": "controller", "/ursim/polyscope.log": "polyscope"}
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_LEVEL = re.compile(r"\b(DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL)\b\s*-\s*(.*)")
# Client connections opening and closing (ours included): background noise, demoted to DEBUG
# so that it never raises an alert.
_SOCKET_NOISE = re.compile(r'^Socket "|TCPServer: Accepted|TCPReceiver closed|object was destroyed|MultiTCPSender send')


def _packets(sock: socket.socket, closed_message: str) -> Iterator[bytes]:
    """Split a UR byte stream into packets, each starting with its size (int32, size included).

    A packet may arrive in several chunks, or several packets at once.

    Raises:
        ConnectionError: when the controller closes the connection.
    """
    buf = b""
    while True:
        while len(buf) < 5 or len(buf) < struct.unpack_from(">i", buf)[0]:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError(closed_message)
            buf += chunk
        size = struct.unpack_from(">i", buf)[0]
        packet, buf = buf[:size], buf[size:]
        yield packet


def parse_realtime(packet: bytes, t: float) -> Sample:
    """Turn a complete Real-Time packet (its 4 size bytes included) into a `Sample`.

    A field beyond the end of the packet (older controller version) is skipped.
    """
    values: dict[str, float] = {}
    for offset, n, names in REALTIME_FIELDS.values():
        if offset + 8 * n <= len(packet):
            values.update(zip(names, struct.unpack_from(f">{n}d", packet, offset), strict=True))
    return Sample(t=t, values=values)


def parse_primary(packet: bytes, t: float) -> FaultCode | None:
    """Turn a Primary-interface packet into a `FaultCode`, or None if it is not a fault.

    Codes are written as on the robot's teach pendant: C<code>A<argument> (e.g. C204A3).
    "Key" messages only count as faults at level ERROR or FATAL. Plain text messages are
    ignored: the same events are already in the controller log.
    """
    if len(packet) < 15 or packet[4] != ROBOT_MESSAGE:
        return None
    _timestamp, source, mtype = struct.unpack_from(">Qbb", packet, 5)
    body = packet[15:]
    src = MESSAGE_SOURCES.get(source, str(source))
    if mtype == ERROR_CODE and len(body) >= 17:
        code, argument, report_level, _data_type, _data = struct.unpack_from(">iiiBI", body)
        text = body[17:].decode("utf-8", "replace").strip("\x00")
        level = REPORT_LEVELS.get(report_level, Level.ERROR)
        return FaultCode(t=t, code=f"C{code}A{argument}", message=f"[{src}] {text}".strip(), level=level)
    if mtype == KEY and len(body) >= 13:
        code, argument, report_level, title_len = struct.unpack_from(">iiiB", body)
        level = REPORT_LEVELS.get(report_level, Level.INFO)
        if level in (Level.ERROR, Level.FATAL):
            title = body[13 : 13 + title_len].decode("utf-8", "replace")
            text = body[13 + title_len :].decode("utf-8", "replace")
            return FaultCode(t=t, code=f"C{code}A{argument}", message=f"[{src}] {title} {text}".strip(), level=level)
    if mtype == RUNTIME_EXCEPTION and len(body) >= 8:
        line, column = struct.unpack_from(">ii", body)
        text = body[8:].decode("utf-8", "replace")
        return FaultCode(t=t, code="RUNTIME_EXCEPTION", message=f"line {line}, column {column}: {text}", level=Level.ERROR)
    return None


def parse_log_line(line: str, source: str) -> LogLine | None:
    """Translate one controller log line; None for an empty line or a `tail` header.

    Colour codes (PolyScope writes some at the start of its lines) are removed first. A line
    without a recognisable level is kept at INFO.

    Formats:
        controller: `2026-10-24_14:00:54:48634 00:00:53:014 INFO    - text`
        PolyScope:  `2026-10-24 14:00:01.875 ERROR -  text {thread: …, loggerClass: …}`
    """
    line = _ANSI.sub("", line).strip()
    if not line or line.startswith("==>"):
        return None
    m = _LEVEL.search(line)
    if not m:
        return LogLine(source=source, text=line)
    level = {"WARN": "WARNING"}.get(m.group(1), m.group(1))
    text = re.sub(r"\s*\{thread:.*\}$", "", m.group(2)).strip()
    if _SOCKET_NOISE.search(text):
        level = "DEBUG"
    return LogLine(level=Level(level), source=source, text=text)


def send_script(host: str, script: str, timeout: float = 5.0) -> None:
    """Send a URScript program to the controller, which runs it at once, replacing any program.

    One line is sent as is; several lines are wrapped in a `def … end` function, the only
    form the controller runs as a program. Nothing comes back on this port: errors show up in
    the log and on the Primary interface, and the end of a move is read on the Dashboard.

    Raises:
        OSError: if the controller cannot be reached.
    """
    lines = [line for line in script.strip().splitlines() if line.strip()]
    if len(lines) > 1:
        body = "\n".join("  " + line for line in lines)
        script = f"def robot_agentic():\n{body}\nend\n"
    else:
        script = lines[0] + "\n"
    with socket.create_connection((host, URSCRIPT_PORT), timeout=timeout) as sock:
        sock.sendall(script.encode())
    log.debug("URScript sent:\n%s", script)


def _joint_round_trip(joint: int, delta_rad: float) -> str:
    """URScript lines that turn one joint by `delta_rad` and bring it back (`q0` must be defined)."""
    return (
        f"q1 = q0\nq1[{joint}] = q0[{joint}] + {delta_rad}\n"
        f"movej(q1, a={JOINT_ACC}, v={JOINT_SPEED})\nmovej(q0, a={JOINT_ACC}, v={JOINT_SPEED})"
    )


class Dashboard:
    """Client of the Dashboard server (text commands, one per line).

    Each command opens its own connection: a few milliseconds more, but no connection to
    restore after a controller restart, which happens often during fault injection.

    Args:
        host: controller address.
        timeout: maximal connection and answer delay, in seconds.
    """

    def __init__(self, host: str, timeout: float = 5.0) -> None:
        """Remember the address; no connection is opened here."""
        self.host, self.timeout = host, timeout

    def send(self, command: str) -> str:
        """Send a command and return the controller's answer, without the line break.

        Raises:
            OSError: if the Dashboard server cannot be reached or does not answer.
        """
        with socket.create_connection((self.host, DASHBOARD_PORT), timeout=self.timeout) as sock:
            sock.recv(1024)  # greeting message
            sock.sendall((command + "\n").encode())
            answer = sock.recv(4096).decode("utf-8", "replace").strip()
        log.debug("dashboard %r → %r", command, answer)
        return answer

    def robot_mode(self) -> str:
        """Robot mode: RUNNING, IDLE, POWER_OFF, DISCONNECTED, etc."""
        return self.send("robotmode").removeprefix("Robotmode: ")

    def safety_status(self) -> str:
        """Safety status: NORMAL, REDUCED, PROTECTIVE_STOP, FAULT, etc."""
        return self.send("safetystatus").removeprefix("Safetystatus: ")

    def power_on_and_release(self, attempts: int = 20) -> str:
        """Bring the robot back to RUNNING: close popups, unlock a protective stop, power on, release brakes.

        Args:
            attempts: number of mode checks, 0.5 s apart.

        Returns:
            The robot mode at the end (RUNNING if everything went well).
        """
        log.info("restarting the robot")
        self.send("close popup")
        self.send("close safety popup")
        if self.safety_status() not in ("NORMAL", "REDUCED"):
            self.send("unlock protective stop")
        self.send("power on")
        time.sleep(1.0)
        self.send("brake release")
        for _ in range(attempts):
            mode = self.robot_mode()
            if mode == "RUNNING":
                return mode
            time.sleep(0.5)
        mode = self.robot_mode()
        log.warning("the robot did not come back to RUNNING (mode %s)", mode)
        return mode


class URSimConnector(Connector):
    """UR3e robot simulated by URSim, running in a Docker container.

    Args:
        host: controller address (ports published by Docker).
        container: container name, to read the log.
        manuals_dir: folder of the machine's manuals.
    """

    name = "ursim_ur3"

    def __init__(self, host: str = "127.0.0.1", container: str = "ursim", manuals_dir: Path = Path("manuals/ursim")) -> None:
        """Remember the addresses; connections open when the streams are first read."""
        self.host = host
        self.container = container
        self.manuals_dir = manuals_dir
        self.dashboard = Dashboard(host)
        # Extra payload (kg) left declared by an interrupted add_load, removed by recover_robot.
        self._payload_offset_kg = 0.0

    def signals(self) -> Iterator[Sample]:
        """Real-Time signals at 500 Hz, stamped on reception (see `REALTIME_FIELDS`).

        Raises:
            ConnectionError: if the controller closes the connection.
            OSError: if the controller cannot be reached or is silent for 2 s.
        """
        with socket.create_connection((self.host, REALTIME_PORT), timeout=2.0) as sock:
            log.info("Real-Time interface connected (%s:%d)", self.host, REALTIME_PORT)
            for packet in _packets(sock, "the controller closed the Real-Time interface"):
                yield parse_realtime(packet, time.time())

    def logs(self) -> Iterator[LogLine]:
        """New lines of the controller and PolyScope logs.

        `tail -q` does not say which file a line comes from: it is deduced from the date format.

        Raises:
            ConnectionError: when `tail` stops (container stopped or restarted).
        """
        cmd = ["docker", "exec", self.container, "tail", "-n", "0", "-q", "-F", *LOG_FILES]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1) as proc:
            log.info("reading the log of container %s", self.container)
            assert proc.stdout is not None
            for raw in proc.stdout:
                clean = _ANSI.sub("", raw)
                source = "polyscope" if re.match(r"\s*\d{4}-\d{2}-\d{2} \d", clean) else "controller"
                entry = parse_log_line(clean, source)
                if entry is not None:
                    yield entry
        raise ConnectionError(f"log reading interrupted (container {self.container} stopped?)")

    def faults(self) -> Iterator[FaultCode]:
        """Fault codes of the Primary interface.

        On connection the controller replays old messages (version, state): everything that
        arrives during the first second is skipped, to keep only new ones.

        Raises:
            ConnectionError: if the controller closes the connection.
            OSError: if the controller cannot be reached.
        """
        with socket.create_connection((self.host, PRIMARY_PORT), timeout=5.0) as sock:
            sock.settimeout(None)
            log.info("Primary interface connected (%s:%d)", self.host, PRIMARY_PORT)
            started = time.time()
            for packet in _packets(sock, "the controller closed the Primary interface"):
                now = time.time()
                fault = parse_primary(packet, now) if now - started >= 1.0 else None
                if fault is not None:
                    log.debug("controller fault: %s", fault)
                    yield fault

    def docs(self) -> list[str]:
        """Manuals present in `manuals_dir` (PDF, HTML, text)."""
        if not self.manuals_dir.exists():
            return []
        return sorted(str(p) for p in self.manuals_dir.iterdir() if p.suffix.lower() in DOC_SUFFIXES)

    def tools(self) -> list[ToolSpec]:
        """Tests offered to the agent, all slow (10 % of the speed) and small."""
        joint = Param("integer", 0, 5, "0 base, 1 shoulder, 2 elbow, 3-5 wrists")
        return [
            ToolSpec(
                name="joint_test",
                description=(
                    "Turns one joint by `delta_deg` degrees at 10 % speed, then brings it back. Every stream is "
                    "recorded during the test. Measures the effort (current, torque) and response of one joint."
                ),
                params={"joint": joint, "delta_deg": Param("number", -5, 5, "amplitude in degrees")},
                moves_machine=True,
            ),
            ToolSpec(
                name="move_hand",
                description=(
                    "Moves the robot's hand (tool) by dx, dy, dz metres in a straight line in the base frame, slowly, "
                    "then brings it back. Tests several joints together on a realistic movement."
                ),
                params={k: Param("number", -0.02, 0.02, "metres") for k in ("dx", "dy", "dz")},
                moves_machine=True,
            ),
            ToolSpec(
                name="add_load",
                description=(
                    "Adds `kg` kilograms to the declared tool payload, repeats a shoulder test (±3°), then restores the "
                    "original payload. Shows how the torque reacts to a known load: separates a payload problem from "
                    "a mechanical one."
                ),
                params={"kg": Param("number", 0, 1.5, "kilograms")},
                moves_machine=True,
            ),
            ToolSpec(
                name="overall_test",
                description=(
                    "Overall test: each joint in turn does a round trip of `amplitude_deg` degrees at 10 % speed. About "
                    "30 seconds. Lets you compare the joints with each other and find the abnormal one."
                ),
                params={"amplitude_deg": Param("number", 1, 5, "degrees")},
                moves_machine=True,
            ),
            ToolSpec(
                name="recover_robot",
                description=(
                    "Brings the robot back to a working state: closes error popups, unlocks a protective stop, powers on "
                    "and releases the brakes. Use it after noting the cause, not to hide it."
                ),
                moves_machine=True,
            ),
        ]

    def call_tool(self, name: str, args: dict[str, Any]) -> str:
        """Run a test (already validated by `core.safety`) and wait for the end of the movement.

        The movement is written in URScript and run by the controller; every test brings the
        robot back to its starting position. The end is detected on the Dashboard (`running`).

        Returns:
            A short summary: test, finished or not, mode and safety status at the end.

        Raises:
            ValueError: if the tool does not exist.
            OSError: if the controller cannot be reached.
        """
        if name == "recover_robot":
            mode = self.dashboard.power_on_and_release()
            if mode == "RUNNING" and self._payload_offset_kg:
                self._restore_payload()
                return f"robot mode: {mode}; payload restored after the interrupted add_load"
            return f"robot mode: {mode}"
        mode = self.dashboard.robot_mode()
        if mode != "RUNNING":
            log.warning("%s impossible: robot in mode %s", name, mode)
            return f"action impossible: the robot is in mode {mode} (use recover_robot once the cause is identified)"

        if name == "joint_test":
            j = int(args["joint"])
            script = f"q0 = get_actual_joint_positions()\n{_joint_round_trip(j, math.radians(float(args['delta_deg'])))}"
            label = f"joint {j} ({JOINT_NAMES[j]}) {float(args['delta_deg']):+}°"
        elif name == "move_hand":
            dx, dy, dz = (float(args[k]) for k in ("dx", "dy", "dz"))
            script = f"""
p0 = get_actual_tcp_pose()
movel(pose_add(p0, p[{dx}, {dy}, {dz}, 0, 0, 0]), a={TOOL_ACC}, v={TOOL_SPEED})
movel(p0, a={TOOL_ACC}, v={TOOL_SPEED})
"""
            label = f"hand dx={dx} dy={dy} dz={dz} m"
        elif name == "add_load":
            # The original payload is read and restored, so as not to erase a wrong payload
            # declaration that may be the very fault under investigation.
            kg = float(args["kg"])
            script = f"""
m0 = get_target_payload()
cog0 = get_target_payload_cog()
set_target_payload(m0 + {kg}, cog0)
sleep(0.2)
q0 = get_actual_joint_positions()
{_joint_round_trip(1, math.radians(3))}
set_target_payload(m0, cog0)
"""
            label = f"payload +{kg} kg"
        elif name == "overall_test":
            d = math.radians(float(args["amplitude_deg"]))
            script = "q0 = get_actual_joint_positions()\n" + "\n".join(_joint_round_trip(j, d) for j in range(6))
            label = f"overall test ±{args['amplitude_deg']}°"
        else:
            raise ValueError(f"unknown tool: {name}")

        log.info("URSim: %s", label)
        send_script(self.host, script)
        # The controller takes a few hundred milliseconds to start the program: without this
        # wait, `running` would still answer "false" and the test would look finished.
        time.sleep(0.3)
        finished = False
        deadline = time.time() + 60.0
        while time.time() < deadline:
            if self.dashboard.send("running").endswith("false"):
                finished = True
                break
            time.sleep(0.1)
        if not finished:
            self.stop()
        mode, safety = self.dashboard.robot_mode(), self.dashboard.safety_status()
        normal = safety in ("NORMAL", "REDUCED")
        summary = f"{label}: {'finished' if finished else 'NOT finished (timeout)'}; mode {mode}, safety {safety}"
        if name == "add_load" and (not finished or not normal):
            # The program stopped before its last line: the extra payload is still declared.
            self._payload_offset_kg = float(args["kg"])
            if mode == "RUNNING" and normal:
                self._restore_payload()
            else:
                summary += f"; the extra {args['kg']} kg payload is still declared, recover_robot will remove it"
        if not finished or not normal:
            log.warning("URSim: %s", summary)
        return summary

    def _restore_payload(self) -> None:
        """Remove the extra payload left declared by an interrupted `add_load`."""
        log.warning("removing %.2f kg of extra payload left by an interrupted add_load", self._payload_offset_kg)
        send_script(self.host, f"set_target_payload(get_target_payload() - {self._payload_offset_kg}, get_target_payload_cog())")
        self._payload_offset_kg = 0.0

    def stop(self) -> None:
        """Stop the current movement (deceleration of 2 rad/s²)."""
        log.warning("movement stop requested")
        send_script(self.host, "stopj(2.0)")

    def snapshot(self) -> dict[str, Any]:
        """Current state of the robot, read on the Dashboard."""
        d = self.dashboard
        return {
            "model": d.send("get robot model"),
            "mode": d.robot_mode(),
            "safety": d.safety_status(),
            "program": d.send("get loaded program"),
            "program state": d.send("programState"),
            "software": d.send("PolyscopeVersion"),
        }

    def health(self) -> list[tuple[bool, str]]:
        """Check the Dashboard, the signal stream, access to the log and the documentation."""
        out: list[tuple[bool, str]] = []
        try:
            mode = self.dashboard.robot_mode()
            out.append((mode == "RUNNING", f"dashboard: robot mode {mode}"))
        except OSError as exc:
            out.append((False, f"dashboard: unreachable ({exc})"))
        try:
            sample = next(iter(self.signals()))
            out.append((True, f"Real-Time interface: {len(sample.values)} channels"))
        except OSError as exc:
            out.append((False, f"Real-Time interface: unreachable ({exc})"))
        try:
            found = subprocess.run(
                ["docker", "exec", self.container, "test", "-f", "/ursim/URControl.log"], capture_output=True, timeout=10
            )
            out.append((found.returncode == 0, "controller log: " + ("readable" if found.returncode == 0 else "file not found")))
        except (OSError, subprocess.TimeoutExpired) as exc:
            out.append((False, f"controller log: unreadable ({exc})"))
        out.append((True, f"documentation: {len(self.docs())} document(s) in {self.manuals_dir}"))
        log.info("health: %s", out)
        return out
