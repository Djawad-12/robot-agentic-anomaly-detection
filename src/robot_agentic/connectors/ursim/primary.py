"""Codes d'erreur et messages du contrôleur, lus sur l'interface primaire (port 30001).

L'interface primaire envoie des paquets « Robot Message » (type 20) dès que le contrôleur
émet un message : code d'erreur, exception d'exécution d'un programme URScript, message
texte… On les traduit en `FaultCode` (erreurs) et `LogLine` (le reste).
Format : documentation Universal Robots « Client Interface », section Robot Message.

Structure d'un paquet Robot Message :
    int32 taille | uint8 type (20) | uint64 horodatage | int8 source | int8 type de message | corps
"""

from __future__ import annotations

import logging
import socket
import struct
import time
from collections.abc import Iterator

from ...core.events import FaultCode, Level, LogLine

log = logging.getLogger(__name__)

PORT = 30001

ROBOT_MESSAGE = 20

# Types de « Robot Message ».
TEXT = 0
ERROR_CODE = 6
KEY = 7
RUNTIME_EXCEPTION = 10

# Niveau de rapport (report level) des codes d'erreur.
REPORT_LEVELS = {0: Level.DEBUG, 1: Level.INFO, 2: Level.WARNING, 3: Level.WARNING, 4: Level.ERROR, 5: Level.FATAL}

# Source du message (robotMessageSource), reprise dans le texte pour situer l'erreur.
SOURCES = {
    -2: "UNDEFINED", -1: "UNKNOWN", 0: "SAFETY", 1: "CONTROLLER", 2: "RTMACHINE", 3: "SIMULATED", 4: "POLYSCOPE",
    5: "PROCESSOR", 8: "CONTROLBOX", 110: "JOINT0", 111: "JOINT1", 112: "JOINT2", 113: "JOINT3", 114: "JOINT4",
    115: "JOINT5", 120: "TOOL",
}  # fmt: skip


def parse(packet: bytes, t: float) -> FaultCode | LogLine | None:
    """Traduit un paquet de l'interface primaire.

    Les codes d'erreur sont écrits comme sur la tablette du robot : C<code>A<argument>
    (ex. C204A3). Les messages « key » ne deviennent des `FaultCode` que s'ils sont de
    niveau ERROR ou FATAL.

    Returns:
        Un `FaultCode`, une `LogLine`, ou None pour un paquet qui n'est pas un message
        (état du robot, version…) ou d'un type de message non retenu.
    """
    if len(packet) < 15 or packet[4] != ROBOT_MESSAGE:
        return None
    _timestamp, source, mtype = struct.unpack_from(">Qbb", packet, 5)
    body = packet[15:]
    src = SOURCES.get(source, str(source))

    if mtype == ERROR_CODE and len(body) >= 17:
        code, argument, report_level, _data_type, _data = struct.unpack_from(">iiiBI", body)
        text = body[17:].decode("utf-8", "replace").strip("\x00")
        return FaultCode(
            t=t, code=f"C{code}A{argument}", message=f"[{src}] {text}".strip(), level=REPORT_LEVELS.get(report_level, Level.ERROR)
        )
    if mtype == KEY and len(body) >= 13:
        code, argument, report_level, title_len = struct.unpack_from(">iiiB", body)
        title = body[13 : 13 + title_len].decode("utf-8", "replace")
        text = body[13 + title_len :].decode("utf-8", "replace")
        level = REPORT_LEVELS.get(report_level, Level.INFO)
        if level in (Level.ERROR, Level.FATAL):
            return FaultCode(t=t, code=f"C{code}A{argument}", message=f"[{src}] {title} {text}".strip(), level=level)
        return LogLine(t=t, level=level, source=f"primary/{src}", text=f"{title} {text}".strip())
    if mtype == RUNTIME_EXCEPTION and len(body) >= 8:
        line, column = struct.unpack_from(">ii", body)
        text = body[8:].decode("utf-8", "replace")
        return FaultCode(t=t, code="RUNTIME_EXCEPTION", message=f"ligne {line}, colonne {column} : {text}", level=Level.ERROR)
    if mtype == TEXT:
        return LogLine(t=t, level=Level.INFO, source=f"primary/{src}", text=body.decode("utf-8", "replace"))
    return None


def stream(host: str, port: int = PORT, timeout: float = 5.0) -> Iterator[FaultCode | LogLine]:
    """Flux des messages du contrôleur, à partir de la connexion.

    À la connexion, le contrôleur renvoie d'anciens messages (version, état) : tout ce qui
    arrive pendant la première seconde est ignoré, pour ne garder que les nouveaux.

    Raises:
        ConnectionError: si le contrôleur ferme la connexion.
        OSError: si le contrôleur est injoignable.
    """
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(None)
        log.info("interface primaire connectée (%s:%d)", host, port)
        started = time.time()
        buf = b""
        while True:
            while len(buf) < 5 or len(buf) < struct.unpack_from(">i", buf)[0]:
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionError("le contrôleur a fermé l'interface primaire")
                buf += chunk
            size = struct.unpack_from(">i", buf)[0]
            packet, buf = buf[:size], buf[size:]
            now = time.time()
            if now - started < 1.0:
                continue
            msg = parse(packet, now)
            if msg is not None:
                log.debug("message du contrôleur : %s", msg)
                yield msg
