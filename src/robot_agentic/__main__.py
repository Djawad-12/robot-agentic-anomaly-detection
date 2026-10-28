"""Entry point: `robot-agentic <command>` (or `python -m robot_agentic <command>`).

Commands:
  health [--api]   checks the machine, its three live streams and (with --api) the Claude API
  check [request]  spot check, or an investigation of a specific request
  loop             continuous monitoring, an investigation at each anomaly
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass

import anthropic

from .agent.graph import run_check, run_loop
from .agent.llm import ClaudeClient
from .agent.tools import Toolbox
from .config import Settings, load_settings, setup_logging
from .connectors.fake import FakeConnector
from .connectors.ursim import URSimConnector
from .core.interfaces import Connector
from .core.recorder import Recorder
from .core.safety import SafetyPolicy
from .hmi import CLI

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    """Everything needed to work on a machine: settings, tools and interface.

    The connector, the recorder and the safety checks are already plugged into `toolbox`.
    """

    settings: Settings
    toolbox: Toolbox
    hmi: CLI


def build_runtime(auto_approve: bool = False, settings: Settings | None = None) -> Runtime:
    """Assemble connector, recorder, safety checks and tools, and start reading the streams.

    Used by the command line and by the benchmarks.

    Args:
        auto_approve: approve every test automatically (simulator only).
        settings: settings; read from the environment if None.

    Raises:
        ValueError: if `settings.connector` is neither "ursim" nor "fake".
    """
    settings = settings or load_settings()
    setup_logging(settings)
    connector: Connector
    if settings.connector == "ursim":
        connector = URSimConnector(settings.robot_host, settings.ursim_container, settings.manuals_dir / "ursim")
    elif settings.connector == "fake":
        connector = FakeConnector()
    else:
        raise ValueError(f"unknown connector: {settings.connector}")
    hmi = CLI(auto_approve=auto_approve)
    recorder = Recorder(connector, settings.recordings_dir).start()
    safety = SafetyPolicy(ask_operator=hmi.confirm, pin=settings.operator_pin, ask_pin=hmi.ask_pin)
    toolbox = Toolbox(connector, recorder, safety, settings.data_dir / "docs_index", settings.recording_token_budget)
    log.info("machine %s, model %s, automatic approval: %s", connector.name, settings.claude_model, auto_approve)
    time.sleep(1.5)  # let the streams fill before the first reading
    return Runtime(settings, toolbox, hmi)


def health(rt: Runtime, check_api: bool) -> bool:
    """Check the machine, the three streams read by the recorder, and optionally the Claude API.

    Returns:
        True if every check passed.
    """
    tb = rt.toolbox
    results = tb.connector.health()
    time.sleep(1.5)
    rec = tb.recorder.latest(2.5)
    results.append((rec.rate_hz > 10, f"signal stream: {rec.rate_hz:.0f} Hz, {len(rec.channels)} channels"))
    for stream in ("logs", "faults"):
        results.append((stream not in tb.recorder.errors, f"{stream} stream: {tb.recorder.errors.get(stream, 'running')}"))
    if check_api:
        try:
            model = anthropic.Anthropic().models.retrieve(rt.settings.claude_model)
            results.append((True, f"Claude API: access to model {model.id}"))
        except anthropic.AuthenticationError:
            results.append((False, "Claude API: missing or invalid key (ANTHROPIC_API_KEY or `ant auth login`)"))
        except anthropic.APIConnectionError as exc:
            results.append((False, f"Claude API: unreachable ({exc})"))
        except anthropic.APIStatusError as exc:
            results.append((False, f"Claude API: error {exc.status_code}"))
    rt.hmi.checklist(results)
    return all(ok for ok, _ in results)


def main(argv: list[str] | None = None) -> int:
    """Read the command line and run the requested command.

    Returns:
        The exit code: 0 on success, 1 if a health check failed.
    """
    parser = argparse.ArgumentParser(prog="robot-agentic", description="Robot Agentic diagnostic agent")
    parser.add_argument("--yes", action="store_true", help="approve every test automatically (simulator only)")
    sub = parser.add_subparsers(dest="command", required=True)
    h = sub.add_parser("health", help="check the machine and the streams")
    h.add_argument("--api", action="store_true", help="also check access to the Claude API (free read request)")
    c = sub.add_parser("check", help="spot check")
    c.add_argument("request", nargs="*", help="specific request (optional)")
    sub.add_parser("loop", help="continuous monitoring")
    args = parser.parse_args(argv)

    rt = build_runtime(auto_approve=args.yes)
    if args.command == "health":
        return 0 if health(rt, args.api) else 1

    st = rt.settings
    llm = ClaudeClient(model=st.claude_model, log_path=st.llm_log)
    try:
        if args.command == "check":
            run_check(rt.toolbox, llm, rt.hmi, st.reports_dir, " ".join(args.request) or None)
        else:
            run_loop(rt.toolbox, llm, rt.hmi, st.reports_dir)
    except KeyboardInterrupt:
        log.warning("interrupted by the operator")
        rt.toolbox.connector.stop()
        rt.hmi.say("Stop requested.")
    u = llm.usage
    rt.hmi.say(
        f"Claude calls: {u.calls}, {u.input_tokens + u.cache_read_tokens} input tokens, {u.output_tokens} output, ≈ ${u.cost_usd:.3f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
