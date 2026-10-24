"""Pilotage et état du robot par le serveur « Dashboard » (port 29999).

Commandes texte, une par ligne : mise sous tension, desserrage des freins, mode du robot,
mode de sécurité, programme en cours… Documentation Universal Robots « Dashboard Server ».
"""

from __future__ import annotations

import socket

PORT = 29999


class Dashboard:
    def __init__(self, host: str, port: int = PORT, timeout: float = 5.0):
        self.host, self.port, self.timeout = host, port, timeout

    def send(self, command: str) -> str:
        # Une connexion par commande : plus lent de quelques millisecondes, mais insensible
        # aux redémarrages du contrôleur, fréquents pendant l'injection de pannes.
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.recv(1024)  # message d'accueil
            sock.sendall((command + "\n").encode())
            return sock.recv(4096).decode("utf-8", "replace").strip()

    def robot_mode(self) -> str:
        return self.send("robotmode").removeprefix("Robotmode: ")

    def safety_status(self) -> str:
        return self.send("safetystatus").removeprefix("Safetystatus: ")

    def snapshot(self) -> dict[str, str]:
        return {
            "modèle": self.send("get robot model"),
            "mode": self.robot_mode(),
            "sécurité": self.safety_status(),
            "programme": self.send("get loaded program"),
            "état du programme": self.send("programState"),
            "logiciel": self.send("PolyscopeVersion"),
        }

    def power_on_and_release(self, attempts: int = 20) -> str:
        """Met le robot sous tension, desserre les freins et attend l'état RUNNING."""
        import time

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
        return self.robot_mode()
