"""Envoi de commandes URScript au contrôleur (interface secondaire, port 30002).

Le contrôleur exécute immédiatement le script reçu ; on attend ensuite la fin du mouvement en
observant la vitesse des articulations dans le flux temps réel.
"""

from __future__ import annotations

import socket

PORT = 30002


def send(host: str, script: str, port: int = PORT, timeout: float = 5.0) -> None:
    """Envoie un programme URScript. Plusieurs lignes sont regroupées dans une fonction."""
    lines = [line for line in script.strip().splitlines() if line.strip()]
    if len(lines) > 1:
        body = "\n".join("  " + line for line in lines)
        script = f"def robot_agentic():\n{body}\nend\n"
    else:
        script = lines[0] + "\n"
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(script.encode())
