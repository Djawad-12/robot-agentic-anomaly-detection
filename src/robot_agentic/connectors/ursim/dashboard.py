"""Pilotage et état du robot par le serveur « Dashboard » (port 29999).

Commandes texte, une par ligne : mise sous tension, desserrage des freins, mode du robot,
mode de sécurité, programme en cours… Documentation Universal Robots « Dashboard Server ».
"""

from __future__ import annotations

import logging
import socket
import time

log = logging.getLogger(__name__)

PORT = 29999


class Dashboard:
    """Client du serveur Dashboard.

    Chaque commande ouvre sa propre connexion : quelques millisecondes de plus, mais aucune
    connexion à rétablir après un redémarrage du contrôleur, fréquent pendant l'injection de
    pannes.

    Args:
        host: adresse du contrôleur.
        port: port du serveur Dashboard.
        timeout: délai maximal de connexion et de réponse, en secondes.
    """

    def __init__(self, host: str, port: int = PORT, timeout: float = 5.0) -> None:
        """Mémorise l'adresse ; aucune connexion n'est ouverte ici."""
        self.host, self.port, self.timeout = host, port, timeout

    def send(self, command: str) -> str:
        """Envoie une commande et renvoie la réponse du contrôleur, sans retour à la ligne.

        Raises:
            OSError: si le serveur Dashboard est injoignable ou ne répond pas.
        """
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.recv(1024)  # message d'accueil
            sock.sendall((command + "\n").encode())
            answer = sock.recv(4096).decode("utf-8", "replace").strip()
        log.debug("dashboard %r → %r", command, answer)
        return answer

    def robot_mode(self) -> str:
        """Mode du robot : RUNNING, IDLE, POWER_OFF, DISCONNECTED, etc."""
        return self.send("robotmode").removeprefix("Robotmode: ")

    def safety_status(self) -> str:
        """État de sécurité : NORMAL, REDUCED, PROTECTIVE_STOP, FAULT, etc."""
        return self.send("safetystatus").removeprefix("Safetystatus: ")

    def snapshot(self) -> dict[str, str]:
        """État courant résumé, tel qu'on le montre à l'agent."""
        return {
            "modèle": self.send("get robot model"),
            "mode": self.robot_mode(),
            "sécurité": self.safety_status(),
            "programme": self.send("get loaded program"),
            "état du programme": self.send("programState"),
            "logiciel": self.send("PolyscopeVersion"),
        }

    def power_on_and_release(self, attempts: int = 20) -> str:
        """Remet le robot en marche et attend l'état RUNNING.

        Ferme les fenêtres d'erreur, lève un arrêt de protection, met sous tension et
        desserre les freins.

        Args:
            attempts: nombre de vérifications du mode, espacées de 0,5 s.

        Returns:
            Le mode du robot à la fin (RUNNING si tout s'est bien passé).
        """
        log.info("remise en marche du robot")
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
        log.warning("le robot n'est pas revenu en marche (mode %s)", mode)
        return mode
