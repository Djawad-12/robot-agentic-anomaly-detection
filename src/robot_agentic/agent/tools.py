"""The agent's tools.

Two families:
- tools common to every machine (state, log, fault codes, documentation, recordings, final
  report), defined here;
- actions specific to the machine, declared by the connector (`Connector.tools()`). Each goes
  through the safety checks (`core.safety`) and is recorded: the agent gets back a summary,
  a plot and the full values of the test.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from ..core import checks
from ..core.events import Level
from ..core.interfaces import Connector
from ..core.recorder import Recorder, Recording
from ..core.safety import SafetyPolicy, SafetyRefusal
from ..docs import DocSearch, build
from . import context

log = logging.getLogger(__name__)

LEVELS = [lvl.value for lvl in Level]

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string", "description": "The root cause, in one or two sentences."},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "evidence": {"type": "array", "items": {"type": "string"}, "description": "The measured facts that support the conclusion."},
        "repair": {"type": "array", "items": {"type": "string"}, "description": "The repair actions, in order."},
        "references": {"type": "array", "items": {"type": "string"}, "description": "Documentation passages quoted."},
        "tests_done": {"type": "array", "items": {"type": "string"}, "description": "Ids of the recordings used."},
    },
    "required": ["root_cause", "confidence", "evidence", "repair", "references", "tests_done"],
    "additionalProperties": False,
}

ToolResult = str | list[dict[str, Any]]


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Definition of a common tool in the API format (closed JSON object)."""
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False},
    }


class Toolbox:
    """Runs the agent's tools on a machine, and keeps track of what was done.

    Attributes:
        report: report handed in by `submit_report` (None until the investigation ends).
        used_recordings: ids of the recordings made during the investigation.

    Args:
        connector: the machine.
        recorder: reader of the streams, already started.
        safety: safety checks applied to every machine action.
        docs_cache: folder of the documentation index cache.
        budget_tokens: budget of one table of values sent to the agent.
    """

    def __init__(self, connector: Connector, recorder: Recorder, safety: SafetyPolicy, docs_cache: Path, budget_tokens: int) -> None:
        """Index the machine's documentation (only once, thanks to the cache)."""
        self.connector = connector
        self.recorder = recorder
        self.safety = safety
        self.budget = budget_tokens
        self.machine_tools = {t.name: t for t in connector.tools()}
        docs = connector.docs()
        self.docs = DocSearch(build(docs, docs_cache)) if docs else None
        self.report: dict[str, Any] | None = None
        self.used_recordings: list[str] = []
        log.info("toolbox ready: %d machine action(s), %d document(s)", len(self.machine_tools), len(docs))

    def definitions(self) -> list[dict[str, Any]]:
        """Definitions of every tool, in the API format: common ones, then the machine's."""
        recording = {"recording_id": {"type": "string"}, "channels": {"type": "array", "items": {"type": "string"}}}
        common = [
            _tool("get_status", "Current state of the machine (mode, safety, program).", {}),
            _tool(
                "get_recent_logs",
                "Log lines of the last `seconds` seconds, at level `min_level` or above.",
                {"seconds": {"type": "number"}, "min_level": {"type": "string", "enum": LEVELS}},
                ["seconds", "min_level"],
            ),
            _tool(
                "get_recent_faults", "Fault codes raised during the last `seconds` seconds.", {"seconds": {"type": "number"}}, ["seconds"]
            ),
            _tool(
                "search_docs",
                "Searches the machine's documentation (manuals). Returns passages with their page, to quote in the report.",
                {"query": {"type": "string"}},
                ["query"],
            ),
            _tool(
                "get_recording",
                'Values of a recording as CSV. `recording_id`: an id returned by a test, or "latest:N" for the last N '
                "seconds of the streams (without moving the machine). Empty `channels`: every channel that varies. "
                "`hz`: wanted rate (0 = the finest the budget allows).",
                {**recording, "hz": {"type": "number"}},
                ["recording_id", "channels", "hz"],
            ),
            _tool("plot_recording", "Plot of the requested channels (8 at most).", recording, ["recording_id", "channels"]),
            {
                "name": "submit_report",
                "description": (
                    "Ends the investigation with the final report. Call it once, when the cause is established "
                    "or no test can help any more."
                ),
                "input_schema": REPORT_SCHEMA,
                "strict": True,
            },
        ]
        machine = [{"name": t.name, "description": t.description, "input_schema": t.input_schema()} for t in self.machine_tools.values()]
        return common + machine

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Run a tool requested by the agent.

        Returns:
            The `tool_result` content: text, or a list of blocks (text and images). A refusal
            by the safety checks comes back as a text starting with "REFUSED", so that the
            agent can adapt.

        Raises:
            ValueError: if the tool does not exist.
            KeyError, FileNotFoundError: if a requested recording or channel does not exist.
        """
        log.info("tool %s %s", name, json.dumps(args, ensure_ascii=False))

        if name in self.machine_tools:
            spec = self.machine_tools[name]
            try:
                self.safety.check(spec, args)
            except SafetyRefusal as exc:
                return f"REFUSED: {exc}"
            if spec.moves_machine:
                label = f"{name}({', '.join(f'{k}={v}' for k, v in args.items())})"
                rec, outcome = self.recorder.record(lambda: self.connector.call_tool(name, args), label=label)
            else:
                result = self.connector.call_tool(name, args)
                if not isinstance(result, Recording):
                    return result
                rec, outcome = result, result.label
                rec.save(self.recorder.directory)
            self.used_recordings.append(rec.id)
            return self.describe_test(rec, outcome)

        match name:
            case "get_status":
                return json.dumps(self.connector.snapshot(), ensure_ascii=False)

            case "get_recent_logs":
                rec = self.recorder.latest(float(args["seconds"]))
                floor = LEVELS.index(args.get("min_level", "INFO"))
                lines = [
                    f"{time.strftime('%H:%M:%S', time.localtime(x.t))} {x.level.value} [{x.source}] {x.text}"
                    for x in rec.logs
                    if LEVELS.index(x.level.value) >= floor
                ]
                return "\n".join(lines[-200:]) or "no line at this level during this period"

            case "get_recent_faults":
                rec = self.recorder.latest(float(args["seconds"]))
                lines = [f"{time.strftime('%H:%M:%S', time.localtime(f.t))} {f.code} {f.level.value} {f.message}" for f in rec.faults]
                return "\n".join(lines) or "no fault code during this period"

            case "search_docs":
                if self.docs is None:
                    return "no documentation available for this machine"
                hits = self.docs.search(args["query"], k=4)
                return "\n\n".join(f"[{p.cite()}]\n{p.text[:1200]}" for _, p in hits) or "no passage found"

            case "get_recording":
                rec = self._load(args["recording_id"])
                names = args.get("channels") or context.moving_channels(rec)
                if args.get("hz"):
                    text = context.to_csv(rec, names, float(args["hz"]))
                    if context.estimate_tokens(text) <= self.budget:
                        return text
                text, rate = context.csv_within_budget(rec, names, self.budget)
                return f"# {rec.id}, {rec.label}, {rec.duration:.1f} s, rate {rate:.0f} Hz (original {rec.rate_hz:.0f} Hz)\n{text}"

            case "plot_recording":
                rec = self._load(args["recording_id"])
                names = args.get("channels") or context.moving_channels(rec)
                return [{"type": "text", "text": f"plot of {rec.id}"}, context.image_block(context.plot(rec, names))]

            case "submit_report":
                self.report = dict(args)
                log.info("report handed in: %s (confidence %s)", self.report.get("root_cause"), self.report.get("confidence"))
                return "report received"

        raise ValueError(f"unknown tool: {name}")

    def describe_test(self, rec: Recording, outcome: str) -> list[dict[str, Any]]:
        """What the agent receives after a test (or when it opens a black box).

        Three blocks: (1) a text with the outcome, the stream cuts, the fault codes and the log
        warnings during the test; (2) a plot of the channels that moved the most; (3) the full
        values of every channel that varies, at the rate the budget allows.
        """
        parts = [f"Recording {rec.id} ({rec.duration:.1f} s, {len(rec.t)} points at {rec.rate_hz:.0f} Hz): {outcome}"]
        gaps = checks.stream_gaps(rec)
        if gaps:
            parts.append(f"Signal stream cuts: {[(round(a, 2), round(b, 2)) for a, b in gaps[:10]]} (start s, duration s)")
        if rec.faults:
            parts.append("Fault codes during the test:\n" + "\n".join(f"- {f.code} {f.message}" for f in rec.faults))
        warnings = [x for x in rec.logs if x.level in (Level.WARNING, Level.ERROR, Level.FATAL)]
        if warnings:
            parts.append("Log (warnings and errors):\n" + "\n".join(f"- {x.level.value} {x.text}" for x in warnings[:30]))
        log.info("test %s: %s; %d cut(s), %d fault code(s)", rec.id, outcome, len(gaps), len(rec.faults))

        blocks: list[dict[str, Any]] = [{"type": "text", "text": "\n\n".join(parts)}]
        moving = context.moving_channels(rec)
        if moving:
            blocks.append(context.image_block(context.plot(rec, moving[:6])))
            csv, rate = context.csv_within_budget(rec, moving, self.budget)
            blocks.append({"type": "text", "text": f"Full values (channels that vary, {rate:.0f} Hz):\n{csv}"})
        return blocks

    def _load(self, recording_id: str) -> Recording:
        """Read back a recording, or make and save the one of the last N seconds for "latest:N"."""
        if recording_id.startswith("latest:"):
            rec = self.recorder.latest(float(recording_id.split(":", 1)[1]), label="recent streams")
            rec.save(self.recorder.directory)
            self.used_recordings.append(rec.id)
            return rec
        return self.recorder.load(recording_id)
