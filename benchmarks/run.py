"""Mesure l'agent sur les scénarios de pannes : diagnostics justes, temps et coût.

Pour chaque scénario de `tools/fault_injection/scenarios.yaml` :
1. remet URSim en état de marche ;
2. injecte la panne ;
3. lance l'agent (réveillé par une alerte, ou en contrôle ponctuel), tests acceptés
   automatiquement puisque la machine est simulée ;
4. compare la cause racine du rapport à la réponse attendue ;
5. répare la panne.

Usage : python -m benchmarks.run [--only id1,id2] [--repeat N]
Chaque appel à Claude est payant : le coût de la série s'affiche à la fin.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import yaml

from robot_agentic.__main__ import build_runtime
from robot_agentic.agent.graph import build_graph
from robot_agentic.agent.llm import ClaudeClient
from robot_agentic.agent.modes import run_check
from tools.fault_injection.inject import URSimFaults

log = logging.getLogger(__name__)

SCENARIOS = Path(__file__).parent.parent / "tools" / "fault_injection" / "scenarios.yaml"
RESULTS = Path(__file__).parent / "results"


def main() -> None:
    """Joue les scénarios demandés, puis affiche et sauvegarde les résultats.

    Notation : un diagnostic est juste si la cause racine du rapport contient au moins un
    mot de chaque groupe de `expected.keywords` (sans tenir compte de la casse).
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="identifiants séparés par des virgules")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    scenarios: list[dict[str, Any]] = yaml.safe_load(SCENARIOS.read_text())
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]

    faults = URSimFaults()
    faults.wait_ready()
    rt = build_runtime(auto_approve=True)
    tb, hmi, st = rt.toolbox, rt.hmi, rt.settings
    rows: list[dict[str, Any]] = []
    for scenario in scenarios * args.repeat:
        llm = ClaudeClient(model=st.model, log_path=st.llm_log)
        hmi.say(f"=== {scenario['id']} ===")
        log.info("scénario %s", scenario["id"])
        started = time.time()
        if scenario["mode"] == "loop":
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
        correct = bool(report) and all(any(k.lower() in root_cause.lower() for k in group) for group in scenario["expected"]["keywords"])
        rows.append({
            "id": scenario["id"],
            "detected": detected,
            "detection_s": detection_s,
            "correct": correct,
            "root_cause": root_cause or None,
            "expected": scenario["expected"]["cause"],
            "seconds": round(elapsed, 1),
            "llm_calls": llm.usage.calls,
            "cost_usd": round(llm.usage.cost_usd, 4),
            "report_path": state.get("report_path"),
        })  # fmt: skip
        log.info("scénario %s : %s en %.0f s, %.3f $", scenario["id"], "juste" if correct else "faux", elapsed, llm.usage.cost_usd)
        faults.revert(scenario["fault"])

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    n = len(rows)
    correct_count = sum(r["correct"] for r in rows)
    print(f"\nDiagnostics justes : {correct_count}/{n} ({100 * correct_count / n:.0f} %)")
    print(f"Temps moyen : {sum(r['seconds'] for r in rows) / n:.0f} s ; coût total : {sum(r['cost_usd'] for r in rows):.3f} $")
    for r in rows:
        print(f"  {'✔' if r['correct'] else '✘'} {r['id']:20} {r['seconds']:>6.0f} s {r['cost_usd']:>7.3f} $  {r['root_cause']}")
    print(f"Résultats : {out}")


if __name__ == "__main__":
    main()
