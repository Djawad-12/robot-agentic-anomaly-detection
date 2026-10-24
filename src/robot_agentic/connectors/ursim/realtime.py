"""Lecture des signaux temps réel par l'interface « Real-Time » du contrôleur (port 30003).

Le contrôleur envoie un paquet binaire toutes les 2 ms (500 Hz) sur les robots e-Series.
Chaque paquet contient l'état complet du robot à cet instant ; on n'en garde que les voies
utiles au diagnostic. Format : documentation Universal Robots « Remote Control Via TCP/IP »,
section Real-Time interface (5.x). Aucune dépendance externe : `struct` suffit.
"""

from __future__ import annotations

import socket
import struct
from collections.abc import Iterator

from ...core.events import Sample

PORT = 30003

# Décalage (en octets, taille du paquet comprise) et nombre de doubles de chaque champ retenu.
FIELDS: dict[str, tuple[int, int]] = {
    "q_target": (12, 6),
    "torque_target": (204, 6),
    "q_actual": (252, 6),
    "qd_actual": (300, 6),
    "current_actual": (348, 6),
    "tcp_pose": (444, 6),
    "tcp_force": (540, 6),
    "motor_temperature": (692, 6),
    "robot_mode": (756, 1),
    "safety_mode": (812, 1),
    "tool_accelerometer": (868, 3),
    "speed_scaling": (940, 1),
    "main_voltage": (972, 1),
    "robot_voltage": (980, 1),
    "robot_current": (988, 1),
    "joint_voltage": (996, 6),
}

TCP_AXES = ["x", "y", "z", "rx", "ry", "rz"]


def channel_names(field: str, n: int) -> list[str]:
    """Noms lisibles des voies : joint_0_current, tcp_x, tool_acc_z, robot_mode…"""
    if n == 1:
        return [field]
    if field.startswith("tcp_"):
        return [f"{field}_{a}" for a in TCP_AXES]
    if field == "tool_accelerometer":
        return [f"tool_acc_{a}" for a in "xyz"]
    return [f"joint_{i}_{field}" for i in range(n)]


def parse(packet: bytes, t: float) -> Sample:
    """Transforme un paquet complet (taille comprise) en `Sample`."""
    values: dict[str, float] = {}
    for field, (offset, n) in FIELDS.items():
        if offset + 8 * n > len(packet):
            continue
        for name, v in zip(channel_names(field, n), struct.unpack_from(f">{n}d", packet, offset), strict=True):
            values[name] = v
    return Sample(t=t, values=values)


def stream(host: str, port: int = PORT, timeout: float = 2.0) -> Iterator[Sample]:
    """Flux de `Sample` à 500 Hz. S'arrête (exception) si la connexion est perdue."""
    import time

    with socket.create_connection((host, port), timeout=timeout) as sock:
        buf = b""
        while True:
            while len(buf) < 4:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("le contrôleur a fermé l'interface temps réel")
                buf += chunk
            size = struct.unpack_from(">i", buf)[0]
            while len(buf) < size:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("le contrôleur a fermé l'interface temps réel")
                buf += chunk
            packet, buf = buf[:size], buf[size:]
            yield parse(packet, time.time())
