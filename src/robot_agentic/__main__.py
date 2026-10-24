"""Point d'entrée : `robot-agentic <commande>` (ou `python -m robot_agentic <commande>`).

Commandes :
  health                 vérifie la machine et les quatre flux
  baseline [--standard]  enregistre les références de bon fonctionnement
  check [demande]        contrôle ponctuel (ou enquête sur une demande précise)
  loop                   surveillance continue, enquête à chaque anomalie
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

from .agent.llm import ClaudeClient
from .agent.modes import run_check, run_loop
from .agent.tools import Toolbox
from .config import Settings, load_settings
from .connectors import load
from .core.recorder import Recorder
from .core.safety import SafetyPolicy
from .hmi.cli import CLI


@dataclass
class Runtime:
    settings: Settings
    toolbox: Toolbox
    hmi: CLI


def build_runtime(auto_approve: bool = False, settings: Settings | None = None) -> Runtime:
    settings = settings or load_settings()
    hmi = CLI(auto_approve=auto_approve)
    connector = load(settings)
    recorder = Recorder(connector, settings.recordings_dir).start()
    safety = SafetyPolicy(ask_operator=hmi.confirm, pin=settings.operator_pin, ask_pin=hmi.ask_pin)
    toolbox = Toolbox(connector, recorder, safety, settings.baselines_dir, settings.data_dir / "docs_index", settings.recording_token_budget)
    time.sleep(1.5)  # laisse les flux se remplir avant la première lecture
    return Runtime(settings, toolbox, hmi)


def standard_tests(toolbox: Toolbox) -> list[tuple[str, dict]]:
    """Tests de référence : un aller-retour de 5° par articulation, puis le test global."""
    tests: list[tuple[str, dict]] = []
    if "joint_test" in toolbox.machine_tools:
        tests += [("joint_test", {"joint": j, "delta_deg": 5}) for j in range(6)]
    if "overall_test" in toolbox.machine_tools:
        tests.append(("overall_test", {"amplitude_deg": 5}))
    return tests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="robot-agentic", description="Agent de diagnostic Robot Agentic")
    parser.add_argument("--yes", action="store_true", help="accepte automatiquement les tests (simulation uniquement)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health", help="vérifie la machine et les flux")
    b = sub.add_parser("baseline", help="enregistre des références de bon fonctionnement")
    b.add_argument("--standard", action="store_true", help="tous les tests de référence")
    b.add_argument("--test", nargs="+", metavar="NOM [clé=valeur…]", help="un test précis, ex. joint_test joint=1 delta_deg=5")
    c = sub.add_parser("check", help="contrôle ponctuel")
    c.add_argument("request", nargs="*", help="demande précise (facultatif)")
    sub.add_parser("loop", help="surveillance continue")
    args = parser.parse_args(argv)

    rt = build_runtime(auto_approve=args.yes)
    tb, hmi, st = rt.toolbox, rt.hmi, rt.settings

    if args.command == "health":
        health = tb.connector.health()
        health["flux"] = {"âge du dernier signal (s)": tb.recorder.last_sample_age(), "erreurs": tb.recorder.errors}
        hmi.info(health)
        return 0 if health.get("ok") else 1

    if args.command == "baseline":
        if args.standard:
            tests = standard_tests(tb)
        elif args.test:
            name, *pairs = args.test
            tests = [(name, {k: float(v) if "." in v else int(v) for k, v in (p.split("=", 1) for p in pairs)})]
        else:
            parser.error("préciser --standard ou --test")
        if not hmi.confirm(f"La machine est-elle en bon état ? {len(tests)} test(s) vont servir de référence."):
            return 1
        for name, targs in tests:
            baseline = tb.capture_baseline(name, targs)
            hmi.say(f"référence enregistrée : {baseline.test} ({baseline.recording_id})")
        return 0

    llm = ClaudeClient(model=st.model, log_path=st.llm_log)
    try:
        if args.command == "check":
            run_check(tb, llm, hmi, st.reports_dir, " ".join(args.request) or None)
        else:
            run_loop(tb, llm, hmi, st.reports_dir)
    except KeyboardInterrupt:
        tb.connector.stop()
        hmi.say("Arrêt demandé.")
    u = llm.usage
    hmi.say(f"Appels à Claude : {u.calls}, {u.input_tokens + u.cache_read_tokens} tokens en entrée, {u.output_tokens} en sortie, ≈ {u.cost_usd:.3f} $")
    return 0


if __name__ == "__main__":
    sys.exit(main())
