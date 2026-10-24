"""Lecture des signaux temps réel par l'interface « Real-Time » du contrôleur (port 30003).

Le contrôleur envoie un paquet binaire toutes les 2 ms (500 Hz) sur les robots e-Series.
Chaque paquet contient l'état complet du robot à cet instant ; on n'en garde que les voies
utiles au diagnostic. Format : documentation Universal Robots « Remote Control Via TCP/IP »,
section Real-Time interface (5.x). Aucune dépendance externe : `struct` suffit.

Ce module est le seul à connaître ce format : le reste du code ne voit que des `Sample`.
"""

from __future__ import annotations

import logging
import socket
import struct
import time
from collections.abc import Iterator

from ...core.events import Sample

log = logging.getLogger(__name__)

PORT = 30003

# Champ retenu → (décalage en octets depuis le début du paquet, taille comprise ; nombre de
# doubles ; noms des voies). Unités UR : rad, rad/s, A, Nm, m, N, °C, V.
_JOINTS = range(6)
_TCP = ["x", "y", "z", "rx", "ry", "rz"]
FIELDS: dict[str, tuple[int, int, list[str]]] = {
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


def parse(packet: bytes, t: float) -> Sample:
    """Transforme un paquet complet (les 4 octets de taille compris) en `Sample`.

    Un champ qui dépasse la fin du paquet (version de contrôleur plus ancienne) est ignoré.
    """
    values: dict[str, float] = {}
    for offset, n, names in FIELDS.values():
        if offset + 8 * n > len(packet):
            continue
        values.update(zip(names, struct.unpack_from(f">{n}d", packet, offset), strict=True))
    return Sample(t=t, values=values)


def stream(host: str, port: int = PORT, timeout: float = 2.0) -> Iterator[Sample]:
    """Flux de `Sample` à 500 Hz, horodatés à la réception.

    Raises:
        ConnectionError: si le contrôleur ferme la connexion.
        OSError: si le contrôleur est injoignable ou ne répond plus pendant `timeout` secondes.
    """
    with socket.create_connection((host, port), timeout=timeout) as sock:
        log.info("interface temps réel connectée (%s:%d)", host, port)
        buf = b""
        while True:
            # Un paquet peut arriver en plusieurs morceaux, ou plusieurs paquets d'un coup :
            # on lit la taille (4 premiers octets), puis jusqu'à avoir le paquet entier.
            while len(buf) < 4 or len(buf) < struct.unpack_from(">i", buf)[0]:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("le contrôleur a fermé l'interface temps réel")
                buf += chunk
            size = struct.unpack_from(">i", buf)[0]
            packet, buf = buf[:size], buf[size:]
            yield parse(packet, time.time())
