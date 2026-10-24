"""Connecteur URSim : le simulateur officiel Universal Robots (phase 1 de la validation).

Assemble les quatre flux et les actions de test :
- signaux : interface temps réel (port 30003, 500 Hz) ;
- journal : fichiers du contrôleur dans le conteneur (`docker exec … tail -F`) ;
- codes d'erreur : interface primaire (port 30001) ;
- documentation : manuels placés dans `manuals/ursim/` ;
- actions : URScript (port 30002), état et récupération par le Dashboard (port 29999).

Toutes les actions qui bougent le robot restent petites et lentes ; leurs bornes sont
déclarées dans `limits` et appliquées par `core.safety` avant tout envoi.
"""

from __future__ import annotations

import logging
import math
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ...core.events import FaultCode, LogLine, Sample, ToolSpec
from ...core.interfaces import Connector
from . import logs, primary, realtime, urscript
from .dashboard import Dashboard

log = logging.getLogger(__name__)

# 10 % de la vitesse articulaire maximale d'un UR3e (π rad/s pour les poignets, on prend la plus faible).
JOINT_SPEED = 0.1 * math.pi  # rad/s
JOINT_ACC = 0.5  # rad/s²
TOOL_SPEED = 0.025  # m/s
TOOL_ACC = 0.25  # m/s²

JOINT_NAMES = ["base", "épaule", "coude", "poignet 1", "poignet 2", "poignet 3"]

DOC_SUFFIXES = {".pdf", ".html", ".htm", ".txt", ".md"}


class URSimConnector(Connector):
    """Robot UR3e simulé par URSim, lancé dans un conteneur Docker.

    Args:
        host: adresse du contrôleur (ports publiés par Docker).
        container: nom du conteneur, pour lire le journal.
        manuals_dir: dossier des manuels de la machine.
    """

    name = "ursim_ur3"

    def __init__(self, host: str = "127.0.0.1", container: str = "ursim", manuals_dir: Path = Path("manuals/ursim")) -> None:
        """Mémorise les adresses ; les connexions s'ouvrent à la première lecture des flux."""
        self.host = host
        self.container = container
        self.manuals_dir = manuals_dir
        self.dashboard = Dashboard(host)

    def signals(self) -> Iterator[Sample]:
        """Signaux temps réel à 500 Hz : voir `realtime.FIELDS` pour la liste des voies."""
        return realtime.stream(self.host)

    def logs(self) -> Iterator[LogLine]:
        """Nouvelles lignes du journal du contrôleur et de PolyScope."""
        return logs.stream(self.container)

    def faults(self) -> Iterator[FaultCode]:
        """Codes d'erreur de l'interface primaire.

        Les messages texte de cette interface sont écartés : les mêmes événements figurent
        déjà dans le journal du contrôleur.
        """
        return (m for m in primary.stream(self.host) if isinstance(m, FaultCode))

    def docs(self) -> list[str]:
        """Manuels présents dans `manuals_dir` (PDF, HTML, texte)."""
        if not self.manuals_dir.exists():
            return []
        return sorted(str(p) for p in self.manuals_dir.iterdir() if p.suffix.lower() in DOC_SUFFIXES)

    def tools(self) -> list[ToolSpec]:
        """Tests proposés à l'agent, tous lents (10 % de la vitesse) et de faible amplitude."""
        joint = {"type": "integer", "minimum": 0, "maximum": 5, "description": "0 base, 1 épaule, 2 coude, 3-5 poignets"}
        return [
            ToolSpec(
                name="joint_test",
                description=(
                    "Fait tourner une articulation de `delta_deg` degrés à 10 % de la vitesse, puis la ramène à sa "
                    "position de départ. Tous les flux sont enregistrés pendant le test. Sert à mesurer l'effort "
                    "(courant, couple) et la réponse d'une articulation précise."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"joint": joint, "delta_deg": {"type": "number", "description": "amplitude, entre -5 et 5"}},
                    "required": ["joint", "delta_deg"],
                    "additionalProperties": False,
                },
                moves_machine=True,
                limits={"joint": (0, 5), "delta_deg": (-5, 5)},
            ),
            ToolSpec(
                name="move_hand",
                description=(
                    "Déplace la main du robot (outil) de dx, dy, dz mètres en ligne droite dans le repère de la base, "
                    "lentement, puis la ramène. Sert à tester plusieurs articulations ensemble sur un mouvement réaliste."
                ),
                input_schema={
                    "type": "object",
                    "properties": {k: {"type": "number", "description": "en mètres, entre -0.02 et 0.02"} for k in ("dx", "dy", "dz")},
                    "required": ["dx", "dy", "dz"],
                    "additionalProperties": False,
                },
                moves_machine=True,
                limits={"dx": (-0.02, 0.02), "dy": (-0.02, 0.02), "dz": (-0.02, 0.02)},
            ),
            ToolSpec(
                name="add_load",
                description=(
                    "Ajoute une charge de `kg` kilogrammes à la déclaration de l'outil, refait un test de l'épaule "
                    "(±3°), puis rétablit la charge d'origine. Montre comment le couple réagit à une charge connue : "
                    "utile pour distinguer un problème de charge d'un problème mécanique."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"kg": {"type": "number", "description": "entre 0 et 1.5"}},
                    "required": ["kg"],
                    "additionalProperties": False,
                },
                moves_machine=True,
                limits={"kg": (0, 1.5)},
            ),
            ToolSpec(
                name="overall_test",
                description=(
                    "Test global : chaque articulation, l'une après l'autre, fait un aller-retour de `amplitude_deg` "
                    "degrés à 10 % de la vitesse. Environ 30 secondes. Sert au contrôle ponctuel et à repérer "
                    "l'articulation anormale quand rien ne l'indique encore."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"amplitude_deg": {"type": "number", "description": "entre 1 et 5"}},
                    "required": ["amplitude_deg"],
                    "additionalProperties": False,
                },
                moves_machine=True,
                limits={"amplitude_deg": (1, 5)},
            ),
            ToolSpec(
                name="recover_robot",
                description=(
                    "Remet le robot en état de marche : ferme les fenêtres d'erreur, lève un arrêt de protection, met "
                    "sous tension et desserre les freins. À utiliser après avoir noté la cause, pas pour la masquer."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                moves_machine=True,
            ),
        ]

    def call_tool(self, name: str, args: dict[str, Any]) -> str:
        """Exécute un test (déjà validé par `core.safety`) et attend la fin du mouvement.

        Le mouvement est écrit en URScript et exécuté par le contrôleur ; chaque test ramène
        le robot à sa position de départ. La fin est détectée sur le Dashboard (`running`).

        Returns:
            Un compte rendu court : test, terminé ou non, mode et état de sécurité à la fin.

        Raises:
            ValueError: si l'outil n'existe pas.
            OSError: si le contrôleur est injoignable.
        """
        if name == "recover_robot":
            return f"mode du robot : {self.dashboard.power_on_and_release()}"
        mode = self.dashboard.robot_mode()
        if mode != "RUNNING":
            log.warning("%s impossible : robot en mode %s", name, mode)
            return f"action impossible : le robot est en mode {mode} (utiliser recover_robot si la cause est identifiée)"

        if name == "joint_test":
            j, d = int(args["joint"]), math.radians(float(args["delta_deg"]))
            script = f"""
q0 = get_actual_joint_positions()
q1 = q0
q1[{j}] = q0[{j}] + {d}
movej(q1, a={JOINT_ACC}, v={JOINT_SPEED})
movej(q0, a={JOINT_ACC}, v={JOINT_SPEED})
"""
            label = f"articulation {j} ({JOINT_NAMES[j]}) {args['delta_deg']:+}°"
        elif name == "move_hand":
            dx, dy, dz = (float(args[k]) for k in ("dx", "dy", "dz"))
            script = f"""
p0 = get_actual_tcp_pose()
movel(pose_add(p0, p[{dx}, {dy}, {dz}, 0, 0, 0]), a={TOOL_ACC}, v={TOOL_SPEED})
movel(p0, a={TOOL_ACC}, v={TOOL_SPEED})
"""
            label = f"main dx={dx} dy={dy} dz={dz} m"
        elif name == "add_load":
            # La charge d'origine est relue puis rétablie, pour ne pas effacer une mauvaise
            # déclaration de charge qui serait justement la panne recherchée.
            kg, d = float(args["kg"]), math.radians(3)
            script = f"""
m0 = get_target_payload()
cog0 = get_target_payload_cog()
set_target_payload(m0 + {kg}, cog0)
sleep(0.2)
q0 = get_actual_joint_positions()
q1 = q0
q1[1] = q0[1] + {d}
movej(q1, a={JOINT_ACC}, v={JOINT_SPEED})
movej(q0, a={JOINT_ACC}, v={JOINT_SPEED})
set_target_payload(m0, cog0)
"""
            label = f"charge +{kg} kg"
        elif name == "overall_test":
            d = math.radians(float(args["amplitude_deg"]))
            moves = "\n".join(
                f"q1 = q0\nq1[{j}] = q0[{j}] + {d}\nmovej(q1, a={JOINT_ACC}, v={JOINT_SPEED})\nmovej(q0, a={JOINT_ACC}, v={JOINT_SPEED})"
                for j in range(6)
            )
            script = f"q0 = get_actual_joint_positions()\n{moves}"
            label = f"test global ±{args['amplitude_deg']}°"
        else:
            raise ValueError(f"outil inconnu : {name}")

        log.info("URSim : %s", label)
        urscript.send(self.host, script)
        # Le contrôleur met quelques centaines de millisecondes à démarrer le programme : sans
        # cette attente, `running` répondrait encore « false » et le test serait jugé fini.
        time.sleep(0.3)
        finished = False
        deadline = time.time() + 60.0
        while time.time() < deadline:
            if self.dashboard.send("running").endswith("false"):
                finished = True
                break
            time.sleep(0.1)
        mode, safety = self.dashboard.robot_mode(), self.dashboard.safety_status()
        if not finished:
            log.warning("URSim : %s non terminé après 60 s (mode %s, sécurité %s)", label, mode, safety)
        return f"{label} : {'terminé' if finished else 'NON terminé (délai dépassé ou arrêt)'} ; mode {mode}, sécurité {safety}"

    def stop(self) -> None:
        """Arrête le mouvement en cours (décélération de 2 rad/s²)."""
        log.warning("arrêt du mouvement demandé")
        urscript.send(self.host, "stopj(2.0)")

    def snapshot(self) -> dict[str, Any]:
        """État courant du robot, lu sur le Dashboard."""
        return self.dashboard.snapshot()

    def health(self) -> dict[str, Any]:
        """Vérifie le Dashboard, le flux de signaux, l'accès au journal et la documentation.

        Returns:
            Une entrée lisible par vérification, et "ok" : True si le robot est en marche,
            les signaux arrivent et le journal est accessible.
        """
        out: dict[str, Any] = {}
        try:
            out["dashboard"] = self.dashboard.robot_mode()
        except OSError as exc:
            out["dashboard"] = f"injoignable ({exc})"
        try:
            sample = next(iter(self.signals()))
            out["signaux"] = f"{len(sample.values)} voies"
        except OSError as exc:
            out["signaux"] = f"injoignable ({exc})"
        try:
            found = subprocess.run(
                ["docker", "exec", self.container, "test", "-f", "/ursim/URControl.log"], capture_output=True, timeout=10
            )
            out["journal"] = "accessible" if found.returncode == 0 else "fichier introuvable"
        except (OSError, subprocess.TimeoutExpired) as exc:
            out["journal"] = f"inaccessible ({exc})"
        out["documentation"] = f"{len(self.docs())} document(s) dans {self.manuals_dir}"
        out["ok"] = out["dashboard"] == "RUNNING" and "voies" in out["signaux"] and out["journal"] == "accessible"
        log.info("état de santé : %s", out)
        return out
