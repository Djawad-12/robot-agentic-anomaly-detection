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
- `report` met en forme et sauvegarde le rapport.
"""

from __future__ import annotations

import json
import queue
import time
from importlib import resources
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ..core.checks import EventWatcher
from ..core.events import Alert
from .llm import LLM
from .tools import Toolbox

MAX_TURNS = 25


class State(TypedDict, total=False):
    mode: str  # "loop" ou "check"
    alert: Alert | None
    approved: bool
    request: str
    report: dict | None
    report_path: str
    turns: int
    error: str


def system_prompt() -> str:
    return resources.files("robot_agentic.agent").joinpath("prompts/system.md").read_text()


def build_graph(toolbox: Toolbox, llm: LLM, hmi: Any, events: queue.Queue | None, reports_dir: Path, watch_timeout: float | None = None):
    watcher = EventWatcher()

    def watch(state: State) -> State:
        assert events is not None, "le mode boucle a besoin du flux d'événements"
        deadline = None if watch_timeout is None else time.time() + watch_timeout
        while True:
            try:
                event = events.get(timeout=1.0)
            except queue.Empty:
                if deadline and time.time() > deadline:
                    return {"alert": None}
                continue
            if alert := watcher.feed(event):
                hmi.alert(alert)
                return {"alert": alert}

    def ask_approval(state: State) -> State:
        alert = state["alert"]
        assert alert is not None
        return {"approved": hmi.confirm(f"Anomalie : {alert.reason}. Lancer une analyse de la cause racine ?")}

    def investigate(state: State) -> State:
        toolbox.safety.reset()
        toolbox.report = None
        tools = toolbox.definitions()
        messages: list[dict] = [{"role": "user", "content": _opening(state, toolbox)}]
        for turn in range(1, MAX_TURNS + 1):
            response = llm.create(system=system_prompt(), messages=messages, tools=tools)
            messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if block.type == "thinking" and getattr(block, "thinking", ""):
                    hmi.thinking(block.thinking)
                elif block.type == "text" and block.text.strip():
                    hmi.say(block.text)
            if response.stop_reason == "refusal":
                return {"turns": turn, "error": "requête déclinée par le modèle", "report": None}
            uses = [b for b in response.content if b.type == "tool_use"]
            if not uses:
                if toolbox.report is not None:
                    break
                messages.append({"role": "user", "content": "Termine l'enquête en appelant submit_report."})
                continue
            results = []
            for use in uses:
                hmi.tool_call(use.name, use.input)
                try:
                    content = toolbox.execute(use.name, dict(use.input))
                    results.append({"type": "tool_result", "tool_use_id": use.id, "content": content})
                except Exception as exc:  # noqa: BLE001 - l'erreur est rendue à l'agent, qui peut s'adapter
                    results.append({"type": "tool_result", "tool_use_id": use.id, "content": f"erreur : {exc}", "is_error": True})
            messages.append({"role": "user", "content": results})
            if toolbox.report is not None:
                return {"turns": turn, "report": toolbox.report}
        return {"turns": MAX_TURNS, "report": toolbox.report, "error": None if toolbox.report else "nombre maximal de tours atteint sans rapport"}

    def report(state: State) -> State:
        reports_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "machine": toolbox.connector.name,
            "mode": state.get("mode"),
            "alert": state["alert"].model_dump(mode="json") if state.get("alert") else None,
            "request": state.get("request"),
            "report": state.get("report"),
            "error": state.get("error"),
            "recordings": toolbox.used_recordings,
            "turns": state.get("turns"),
        }
        path = reports_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
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


def _opening(state: State, toolbox: Toolbox) -> str:
    """Premier message de l'enquête : le contexte que l'agent n'a pas à aller chercher."""
    status = toolbox.execute("get_status", {})
    faults = toolbox.execute("get_recent_faults", {"seconds": 120})
    logs = toolbox.execute("get_recent_logs", {"seconds": 120, "min_level": "WARNING"})
    baselines = toolbox.execute("list_baselines", {})
    if state.get("alert"):
        a = state["alert"]
        head = f"Anomalie détectée : {a.reason} (gravité {a.severity.value}).\nIndices :\n" + "\n".join(f"- {e}" for e in a.evidence)
    else:
        head = state.get("request") or "Contrôle ponctuel : vérifie que la machine fonctionne normalement et détecte tout problème naissant."
    return (
        f"{head}\n\nMachine : {toolbox.connector.name}\nÉtat : {status}\n\nCodes d'erreur (2 dernières minutes) :\n{faults}\n\n"
        f"Journal, avertissements et erreurs (2 dernières minutes) :\n{logs}\n\nRéférences disponibles :\n{baselines}"
    )
