"""Measures the agent on the fault scenarios: correct diagnoses, time and cost.

For each scenario of `tools/fault_injection/scenarios.yaml`:
1. brings URSim back to a working state;
2. injects the fault;
3. runs the agent (woken up by an alert, or as a spot check), with tests approved
   automatically since the machine is simulated;
4. grades the report's root cause twice: by keywords, and by a Claude judge that compares
   it with the expected cause;
5. repairs the fault.

Usage: python -m benchmarks.run [--only id1,id2] [--repeat N]
Every call to Claude is paid: the cost of the series is shown at the end.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import anthropic
import yaml

from robot_agentic.__main__ import build_runtime
from robot_agentic.agent.graph import build_graph, run_check
from robot_agentic.agent.llm import ClaudeClient, cost_usd
from tools.fault_injection.inject import URSimFaults

log = logging.getLogger(__name__)

SCENARIOS = Path(__file__).parent.parent / "tools" / "fault_injection" / "scenarios.yaml"
RESULTS = Path(__file__).parent / "results"

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"correct": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["correct", "reason"],
    "additionalProperties": False,
}


def judge(client: anthropic.Anthropic, model: str, expected: str, root_cause: str) -> tuple[bool, str, float]:
    """Ask Claude whether the diagnosed root cause matches the expected one.

    Returns:
        The verdict, the judge's one-sentence reason, and the cost of the call in dollars.
    """
    response = client.messages.create(
        model=model,
        max_tokens=4000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": (
                    "You grade a robot diagnostic agent. Does the diagnosed root cause identify the same root cause "
                    "as the expected one? Wording may differ; extra correct detail is fine; a different or vaguer "
                    "cause is wrong. Give a one-sentence reason.\n\n"
                    f"Expected root cause: {expected}\n\nDiagnosed root cause: {root_cause}"
                ),
            }
        ],
    )
    verdict = json.loads(next(b.text for b in response.content if b.type == "text"))
    return bool(verdict["correct"]), verdict["reason"], cost_usd(model, response.usage)


def main() -> None:
    """Play the requested scenarios, then show and save the results."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated scenario ids")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    scenarios: list[dict[str, Any]] = yaml.safe_load(SCENARIOS.read_text())
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]

    rt = build_runtime(auto_approve=True)
    tb, hmi, st = rt.toolbox, rt.hmi, rt.settings
    faults = URSimFaults(st.robot_host, st.ursim_container)
    faults.wait_ready()
    judge_client = anthropic.Anthropic()
    rows: list[dict[str, Any]] = []
    for scenario in scenarios * args.repeat:
        llm = ClaudeClient(model=st.claude_model, log_path=st.llm_log)
        hmi.say(f"=== {scenario['id']} ===")
        log.info("scenario %s", scenario["id"])
        started = time.time()
        if scenario["mode"] == "loop":
            # Subscribe before injecting, so that no event of the fault is missed.
            events = tb.recorder.subscribe()
            graph = build_graph(tb, llm, hmi, events=events, reports_dir=st.reports_dir, watch_timeout=60)
            injected_at = time.time()
            faults.inject(scenario["fault"])
            state = graph.invoke({"mode": "loop"})
            alert = state.get("alert")
            detected, detection_s = alert is not None, (alert.t - injected_at) if alert else None
        else:
            faults.inject(scenario["fault"])
            time.sleep(3)
            state = run_check(tb, llm, hmi, st.reports_dir)
            detected, detection_s = True, None
        elapsed = time.time() - started

        report = state.get("report")
        root_cause = report["root_cause"] if report else ""
        expected = scenario["expected"]
        keywords_ok = bool(report) and all(any(k.lower() in root_cause.lower() for k in group) for group in expected["keywords"])
        judge_ok, judge_reason, judge_cost = (
            judge(judge_client, st.claude_model, expected["cause"], root_cause) if report else (False, "no report", 0.0)
        )
        rows.append({
            "id": scenario["id"],
            "detected": detected,
            "detection_s": detection_s,
            "keywords_ok": keywords_ok,
            "judge_ok": judge_ok,
            "judge_reason": judge_reason,
            "root_cause": root_cause or None,
            "expected": expected["cause"],
            "seconds": round(elapsed, 1),
            "llm_calls": llm.usage.calls,
            "cost_usd": round(llm.usage.cost_usd + judge_cost, 4),
            "report_path": state.get("report_path"),
        })  # fmt: skip
        log.info("scenario %s: keywords %s, judge %s, %.0f s", scenario["id"], keywords_ok, judge_ok, elapsed)
        faults.revert(scenario["fault"])

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    n = len(rows)
    print(f"\nCorrect diagnoses: keywords {sum(r['keywords_ok'] for r in rows)}/{n}, judge {sum(r['judge_ok'] for r in rows)}/{n}")
    print(f"Mean time: {sum(r['seconds'] for r in rows) / n:.0f} s; total cost: ${sum(r['cost_usd'] for r in rows):.3f}")
    for r in rows:
        marks = ("✔" if r["keywords_ok"] else "✘") + ("✔" if r["judge_ok"] else "✘")
        print(f"  {marks} {r['id']:20} {r['seconds']:>6.0f} s ${r['cost_usd']:>7.3f}  {r['root_cause']}")
    print(f"Results: {out}")


if __name__ == "__main__":
    main()
