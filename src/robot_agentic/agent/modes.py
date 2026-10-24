"""Les deux modes de l'agent.

- `loop` (surveillance) : seuls le journal et les codes d'erreur sont suivis, par les
  vérifications déterministes, sans appel au LLM. Dès qu'une anomalie apparaît, l'opérateur
  est invité à lancer une analyse de la cause racine ; l'agent a alors accès aux signaux
  bruts et à tous les outils. Puis la surveillance reprend.
- `check` (contrôle ponctuel) : la même enquête, lancée à la demande (par exemple chaque
  matin), pour détecter un problème avant la panne.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..hmi import HMI
from .graph import State, build_graph
from .llm import LLM
from .tools import Toolbox

log = logging.getLogger(__name__)


def run_check(toolbox: Toolbox, llm: LLM, hmi: HMI, reports_dir: Path, request: str | None = None) -> State:
    """Lance un contrôle ponctuel, ou une enquête sur une demande précise de l'opérateur.

    Returns:
        L'état final du graphe (`report`, `report_path`, `error`…).
    """
    graph = build_graph(toolbox, llm, hmi, events=None, reports_dir=reports_dir)
    return graph.invoke({"mode": "check", "request": request, "alert": None})


def run_loop(toolbox: Toolbox, llm: LLM, hmi: HMI, reports_dir: Path, max_incidents: int | None = None) -> None:
    """Surveille la machine et enquête à chaque anomalie acceptée par l'opérateur.

    Args:
        toolbox: outils de l'agent sur la machine, enregistreur démarré.
        llm: client Claude.
        hmi: interface avec l'opérateur.
        reports_dir: dossier des rapports.
        max_incidents: nombre d'alertes à traiter avant de rendre la main ; None pour
            surveiller jusqu'à l'interruption (Ctrl+C).
    """
    events = toolbox.recorder.subscribe()
    graph = build_graph(toolbox, llm, hmi, events=events, reports_dir=reports_dir)
    hmi.say("Surveillance en cours (journal et codes d'erreur). Ctrl+C pour arrêter.")
    handled = 0
    while max_incidents is None or handled < max_incidents:
        graph.invoke({"mode": "loop"})
        handled += 1
        log.info("incident %d traité, reprise de la surveillance", handled)
