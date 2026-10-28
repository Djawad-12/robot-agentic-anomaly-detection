"""The course of an investigation, as a LangGraph graph, and the agent's two modes.

    watch ──alert──▶ ask_approval ──yes──▶ investigate ──▶ report
                          │                  ▲    │
                          no                 └────┘
                          ▼          (hypothesis → tool → result)
                         end

- `watch` waits for an alert from the deterministic checks (loop mode); in check mode the
  graph starts directly at `investigate`.
- `ask_approval` asks the operator whether to run a root-cause analysis.
- `investigate` is the loop of calls to Claude: at each turn Claude calls tools, receives
  their results, and starts again until `submit_report`.
- `report` saves the report and shows it.

Modes:
- `run_loop` (monitoring): only the log and the fault codes are watched, by the
  deterministic checks, without calling the LLM. When an anomaly appears, the operator is
  offered a root-cause analysis. Then monitoring resumes.
- `run_check` (spot check): the same investigation, started on demand (e.g. every morning),
  to find a problem before it becomes a breakdown.
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
from ..hmi import CLI
from .llm import LLM
from .tools import Toolbox

log = logging.getLogger(__name__)

MAX_TURNS = 25

SYSTEM_PROMPT = resources.files("robot_agentic.agent").joinpath("prompts/system.md").read_text()


class State(TypedDict, total=False):
    """State shared by the nodes of the graph.

    Attributes:
        mode: "loop" (monitoring) or "check" (spot check).
        alert: the alert that woke the agent up (None in check mode).
        approved: the operator's answer to the analysis proposal.
        request: the operator's specific request in check mode (optional).
        report: report handed in by the agent.
        report_path: file where the report was saved.
        turns: number of calls to Claude.
        error: why the investigation ended without a report.
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
    hmi: CLI,
    events: queue.Queue[LogLine | FaultCode] | None,
    reports_dir: Path,
    watch_timeout: float | None = None,
) -> Any:
    """Build the graph of an investigation.

    Args:
        toolbox: the agent's tools on the machine.
        llm: Claude client (or a fake LLM in the tests).
        hmi: interface with the operator.
        events: queue of new log lines and fault codes (`Recorder.subscribe`), required in
            loop mode.
        reports_dir: folder of the reports.
        watch_timeout: in loop mode, give up watching without an alert after this delay
            (seconds); None to wait forever.

    Returns:
        The compiled graph; `invoke({"mode": "loop"})` or `invoke({"mode": "check", ...})`.
    """
    watcher = EventWatcher()

    def watch(state: State) -> State:
        """Read the events until the first alert (or until `watch_timeout`)."""
        assert events is not None, "loop mode needs the event queue"
        deadline = None if watch_timeout is None else time.time() + watch_timeout
        log.info("monitoring: waiting for an anomaly")
        while True:
            try:
                event = events.get(timeout=1.0)
            except queue.Empty:
                if deadline and time.time() > deadline:
                    log.info("monitoring ended without an anomaly (%.0f s delay)", watch_timeout)
                    return {"alert": None}
                continue
            alert = watcher.feed(event)
            if alert:
                hmi.alert(alert)
                return {"alert": alert}

    def ask_approval(state: State) -> State:
        """Offer the root-cause analysis to the operator."""
        alert = state["alert"]
        assert alert is not None
        approved = hmi.confirm(f"Anomaly: {alert.reason}. Start a root-cause analysis?")
        log.info("analysis %s by the operator", "accepted" if approved else "declined")
        return {"approved": approved}

    def investigate(state: State) -> State:
        """Investigation loop with Claude, until the report or `MAX_TURNS` turns.

        The first message gives the agent the context it does not have to fetch: the alert or
        the request, the machine's state, the fault codes and the log of the last two minutes.
        The history is never modified, only extended: this is what allows prompt caching and
        keeps Claude's reasoning from one turn to the next.
        """
        toolbox.safety.reset()
        toolbox.report = None
        tools = toolbox.definitions()

        alert = state.get("alert")
        if alert:
            head = f"Anomaly detected: {alert.reason} (severity {alert.severity.value}).\nEvidence:\n" + "\n".join(
                f"- {e}" for e in alert.evidence
            )
        else:
            head = state.get("request") or "Spot check: verify that the machine works normally and detect any emerging problem."
        opening = (
            f"{head}\n\nMachine: {toolbox.connector.name}\n"
            f"State: {toolbox.execute('get_status', {})}\n\n"
            f"Fault codes (last 2 minutes):\n{toolbox.execute('get_recent_faults', {'seconds': 120})}\n\n"
            f"Log, warnings and errors (last 2 minutes):\n{toolbox.execute('get_recent_logs', {'seconds': 120, 'min_level': 'WARNING'})}"
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": opening}]
        log.info("investigation started (%s)", "alert: " + alert.reason if alert else state.get("request") or "spot check")

        for turn in range(1, MAX_TURNS + 1):
            response = llm.create(system=SYSTEM_PROMPT, messages=messages, tools=tools)
            messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if block.type == "thinking" and getattr(block, "thinking", ""):
                    hmi.thinking(block.thinking)
                elif block.type == "text" and block.text.strip():
                    hmi.say(block.text)
            if response.stop_reason == "refusal":
                log.error("request declined by the model at turn %d", turn)
                return {"turns": turn, "error": "request declined by the model", "report": None}

            uses = [b for b in response.content if b.type == "tool_use"]
            if not uses:
                # The agent answered without a tool or a report (end of turn or token limit):
                # remind it that it must conclude with submit_report.
                log.warning("turn %d without a tool call (stop: %s)", turn, response.stop_reason)
                messages.append({"role": "user", "content": "End the investigation by calling submit_report."})
                continue

            results = []
            for use in uses:
                hmi.tool_call(use.name, use.input)
                try:
                    content = toolbox.execute(use.name, dict(use.input))
                    results.append({"type": "tool_result", "tool_use_id": use.id, "content": content})
                except Exception as exc:  # noqa: BLE001 - the error is given back to the agent, which can adapt
                    log.exception("tool %s failed", use.name)
                    results.append({"type": "tool_result", "tool_use_id": use.id, "content": f"error: {exc}", "is_error": True})
            messages.append({"role": "user", "content": results})
            if toolbox.report is not None:
                log.info("investigation finished in %d turn(s)", turn)
                return {"turns": turn, "report": toolbox.report, "error": None}

        log.error("investigation stopped after %d turns without a report", MAX_TURNS)
        return {"turns": MAX_TURNS, "report": None, "error": "maximum number of turns reached without a report"}

    def report(state: State) -> State:
        """Save the report (with the alert, the request and the recordings) and show it."""
        reports_dir.mkdir(parents=True, exist_ok=True)
        alert = state.get("alert")
        payload = {
            "machine": toolbox.connector.name,
            "mode": state.get("mode"),
            "alert": {"reason": alert.reason, "severity": alert.severity.value, "evidence": alert.evidence, "t": alert.t}
            if alert
            else None,
            "request": state.get("request"),
            "report": state.get("report"),
            "error": state.get("error"),
            "recordings": toolbox.used_recordings,
            "turns": state.get("turns"),
        }
        path = reports_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        log.info("report saved: %s", path)
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


def run_check(toolbox: Toolbox, llm: LLM, hmi: CLI, reports_dir: Path, request: str | None = None) -> State:
    """Run a spot check, or an investigation of a specific request from the operator.

    Returns:
        The final state of the graph (`report`, `report_path`, `error`…).
    """
    graph = build_graph(toolbox, llm, hmi, events=None, reports_dir=reports_dir)
    return graph.invoke({"mode": "check", "request": request, "alert": None})


def run_loop(toolbox: Toolbox, llm: LLM, hmi: CLI, reports_dir: Path) -> None:
    """Watch the machine and investigate every anomaly the operator accepts, until Ctrl+C.

    After each investigation, the events received meanwhile are dropped: they were mostly
    caused by the agent's own tests, and the agent has already seen them in its recordings.
    """
    events = toolbox.recorder.subscribe()
    graph = build_graph(toolbox, llm, hmi, events=events, reports_dir=reports_dir)
    hmi.say("Monitoring (log and fault codes). Ctrl+C to stop.")
    handled = 0
    while True:
        graph.invoke({"mode": "loop"})
        dropped = 0
        while not events.empty():
            events.get_nowait()
            dropped += 1
        handled += 1
        log.info("incident %d handled, %d event(s) dropped, monitoring resumes", handled, dropped)
