"""Le déroulé d'une enquête, sous forme de graphe LangGraph.

    surveiller ──alerte──▶ demander l'accord ──oui──▶ enquêter ──▶ rapport
                                   │                   ▲   │
                                   non                 └───┘
                                   ▼        (hypothèse → outil → résultat)
                                  fin

- `watch` attend une alerte des vérifications déterministes (mode boucle) ; en contrôle
  ponctuel, le graphe commence directement à `investigate`.
- `ask_approval` demande à l'opérateur s'il veut une analyse de la cause racine ; c'est à
  partir de là que l'agent a accès aux signaux bruts et à tous les outils.
- `investigate` est la boucle d'appels à Claude : à chaque tour, Claude appelle des outils,
  reçoit leurs résultats, et recommence jusqu'à `submit_report`.
- `report` sauvegarde le rapport et l'affiche.
"""

from __future__ import annotations

import json
import logging
import queue
import time
from importlib import resources
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ..core.checks import EventWatcher
from ..core.events import Alert, FaultCode, LogLine
from ..hmi import HMI
from .llm import LLM
from .tools import Toolbox

log = logging.getLogger(__name__)

MAX_TURNS = 25

SYSTEM_PROMPT = resources.files("robot_agentic.agent").joinpath("prompts/system.md").read_text()


class State(TypedDict, total=False):
    """État partagé par les nœuds du graphe.

    Attributes:
        mode: "loop" (surveillance) ou "check" (contrôle ponctuel).
        alert: l'alerte qui a réveillé l'agent (None en contrôle ponctuel).
        approved: réponse de l'opérateur à la proposition d'analyse.
        request: demande précise de l'opérateur en contrôle ponctuel (facultative).
        report: rapport remis par l'agent.
        report_path: fichier où le rapport a été sauvegardé.
        turns: nombre d'appels à Claude.
        error: raison de l'échec si l'enquête finit sans rapport.
    """

    mode: str
    alert: Alert | None
    approved: bool
    request: str | None
    report: dict[str, Any] | None
    report_path: str
    turns: int
    error: str | None


def build_graph(
    toolbox: Toolbox,
    llm: LLM,
    hmi: HMI,
    events: queue.Queue[LogLine | FaultCode] | None,
    reports_dir: Path,
    watch_timeout: float | None = None,
) -> Any:
    """Construit le graphe d'une enquête.

    Args:
        toolbox: outils de l'agent sur la machine.
        llm: client Claude (ou faux LLM dans les tests).
        hmi: interface avec l'opérateur.
        events: file des nouvelles lignes de journal et codes d'erreur (`Recorder.subscribe`),
            indispensable en mode boucle.
        reports_dir: dossier des rapports.
        watch_timeout: en mode boucle, abandonne la surveillance sans alerte après ce délai
            (secondes) ; None pour attendre indéfiniment.

    Returns:
        Le graphe compilé ; `invoke({"mode": "loop"})` ou `invoke({"mode": "check", ...})`.
    """
    watcher = EventWatcher()

    def watch(state: State) -> State:
        """Lit les événements jusqu'à la première alerte (ou jusqu'à `watch_timeout`)."""
        assert events is not None, "le mode boucle a besoin du flux d'événements"
        deadline = None if watch_timeout is None else time.time() + watch_timeout
        log.info("surveillance : en attente d'une anomalie")
        while True:
            try:
                event = events.get(timeout=1.0)
            except queue.Empty:
                if deadline and time.time() > deadline:
                    log.info("surveillance terminée sans anomalie (délai de %.0f s)", watch_timeout)
                    return {"alert": None}
                continue
            alert = watcher.feed(event)
            if alert:
                hmi.alert(alert)
                return {"alert": alert}

    def ask_approval(state: State) -> State:
        """Propose l'analyse de la cause racine à l'opérateur."""
        alert = state["alert"]
        assert alert is not None
        approved = hmi.confirm(f"Anomalie : {alert.reason}. Lancer une analyse de la cause racine ?")
        log.info("analyse %s par l'opérateur", "acceptée" if approved else "refusée")
        return {"approved": approved}

    def investigate(state: State) -> State:
        """Boucle d'enquête avec Claude, jusqu'au rapport ou à `MAX_TURNS` tours.

        Le premier message donne à l'agent le contexte qu'il n'a pas à aller chercher :
        l'alerte ou la demande, l'état de la machine, les codes d'erreur et le journal des
        deux dernières minutes, et les références disponibles. L'historique n'est jamais
        modifié, seulement complété : c'est ce qui permet la mise en cache et la reprise de
        la réflexion de Claude d'un tour à l'autre.
        """
        toolbox.safety.reset()
        toolbox.report = None
        tools = toolbox.definitions()

        alert = state.get("alert")
        if alert:
            head = f"Anomalie détectée : {alert.reason} (gravité {alert.severity.value}).\nIndices :\n" + "\n".join(
                f"- {e}" for e in alert.evidence
            )
        else:
            head = (
                state.get("request")
                or "Contrôle ponctuel : vérifie que la machine fonctionne normalement et détecte tout problème naissant."
            )
        opening = (
            f"{head}\n\nMachine : {toolbox.connector.name}\n"
            f"État : {toolbox.execute('get_status', {})}\n\n"
            f"Codes d'erreur (2 dernières minutes) :\n{toolbox.execute('get_recent_faults', {'seconds': 120})}\n\n"
            "Journal, avertissements et erreurs (2 dernières minutes) :\n"
            f"{toolbox.execute('get_recent_logs', {'seconds': 120, 'min_level': 'WARNING'})}\n\n"
            f"Références disponibles :\n{toolbox.execute('list_baselines', {})}"
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": opening}]
        log.info("enquête lancée (%s)", "alerte : " + alert.reason if alert else state.get("request") or "contrôle ponctuel")

        for turn in range(1, MAX_TURNS + 1):
            response = llm.create(system=SYSTEM_PROMPT, messages=messages, tools=tools)
            messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if block.type == "thinking" and getattr(block, "thinking", ""):
                    hmi.thinking(block.thinking)
                elif block.type == "text" and block.text.strip():
                    hmi.say(block.text)
            if response.stop_reason == "refusal":
                log.error("requête déclinée par le modèle au tour %d", turn)
                return {"turns": turn, "error": "requête déclinée par le modèle", "report": None}

            uses = [b for b in response.content if b.type == "tool_use"]
            if not uses:
                # L'agent a répondu sans outil ni rapport (fin de tour ou limite de tokens) :
                # on lui rappelle qu'il doit conclure par submit_report.
                log.warning("tour %d sans appel d'outil (fin : %s)", turn, response.stop_reason)
                messages.append({"role": "user", "content": "Termine l'enquête en appelant submit_report."})
                continue

            results = []
            for use in uses:
                hmi.tool_call(use.name, use.input)
                try:
                    content = toolbox.execute(use.name, dict(use.input))
                    results.append({"type": "tool_result", "tool_use_id": use.id, "content": content})
                except Exception as exc:  # noqa: BLE001 - l'erreur est rendue à l'agent, qui peut s'adapter
                    log.exception("échec de l'outil %s", use.name)
                    results.append({"type": "tool_result", "tool_use_id": use.id, "content": f"erreur : {exc}", "is_error": True})
            messages.append({"role": "user", "content": results})
            if toolbox.report is not None:
                log.info("enquête terminée en %d tour(s)", turn)
                return {"turns": turn, "report": toolbox.report, "error": None}

        log.error("enquête arrêtée après %d tours sans rapport", MAX_TURNS)
        return {"turns": MAX_TURNS, "report": None, "error": "nombre maximal de tours atteint sans rapport"}

    def report(state: State) -> State:
        """Sauvegarde le rapport (avec l'alerte, la demande et les enregistrements) et l'affiche."""
        reports_dir.mkdir(parents=True, exist_ok=True)
        alert = state.get("alert")
        payload = {
            "machine": toolbox.connector.name,
            "mode": state.get("mode"),
            "alert": alert.model_dump(mode="json") if alert else None,
            "request": state.get("request"),
            "report": state.get("report"),
            "error": state.get("error"),
            "recordings": toolbox.used_recordings,
            "turns": state.get("turns"),
        }
        path = reports_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        log.info("rapport sauvegardé : %s", path)
        hmi.report(state.get("report"), state.get("error"), path)
        return {"report_path": str(path)}

    g = StateGraph(State)
    g.add_node("watch", watch)
    g.add_node("ask_approval", ask_approval)
    g.add_node("investigate", investigate)
    g.add_node("report", report)
    g.set_conditional_entry_point(lambda s: "watch" if s["mode"] == "loop" else "investigate")
    g.add_conditional_edges("watch", lambda s: "ask_approval" if s.get("alert") else END)
    g.add_conditional_edges("ask_approval", lambda s: "investigate" if s.get("approved") else END)
    g.add_edge("investigate", "report")
    g.add_edge("report", END)
    return g.compile()
