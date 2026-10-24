"""Envoi de commandes URScript au contrôleur (interface secondaire, port 30002).

Le contrôleur exécute immédiatement le script reçu, en remplaçant tout programme en cours.
Il ne renvoie rien sur ce port : les erreurs apparaissent dans le journal et sur l'interface
primaire, et la fin d'un mouvement se lit sur le Dashboard (commande `running`).
"""

from __future__ import annotations

import logging
import socket

log = logging.getLogger(__name__)

PORT = 30002


def send(host: str, script: str, port: int = PORT, timeout: float = 5.0) -> None:
    """Envoie un programme URScript au contrôleur.

    Une seule ligne est envoyée telle quelle ; plusieurs lignes sont regroupées dans une
    fonction `def … end`, seule forme que le contrôleur exécute comme un programme.

    Raises:
        OSError: si le contrôleur est injoignable.
    """
    lines = [line for line in script.strip().splitlines() if line.strip()]
    if len(lines) > 1:
        body = "\n".join("  " + line for line in lines)
        script = f"def robot_agentic():\n{body}\nend\n"
    else:
        script = lines[0] + "\n"
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(script.encode())
    log.debug("URScript envoyé :\n%s", script)
