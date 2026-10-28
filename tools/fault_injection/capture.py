"""Captures everything URSim produces when a fault is provoked: the evidence for the fault catalogue.

For one fault, the capture:
1. notes how long each log file of the container is, and which black boxes ("flight
   reports") already exist;
2. starts reading the live streams (signals at 500 Hz, fault codes of the Primary interface);
3. provokes the fault and waits `seconds`;
4. keeps the new raw lines of each log file (unparsed: the evidence must be exactly what URSim
   wrote), the fault codes, the changes of robot mode / safety mode / speed scaling in the
   signals, the cuts of the signal stream, the new black boxes and the Dashboard state;
5. repairs the fault and measures how long the robot takes to be RUNNING again.

The result is one JSON file per capture in `data/catalogue/`, and the recording of the signals
next to it.

Usage:
    python -m tools.fault_injection.capture <fault> [--seconds 15]
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import time
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from robot_agentic.config import load_settings, setup_logging
from robot_agentic.connectors.ursim import URSimConnector, line_time, send_script
from robot_agentic.core.checks import stream_gaps
from robot_agentic.core.recorder import Recorder

from .inject import FAULTS, URSimFaults

log = logging.getLogger(__name__)

LOG_FILES = ["/ursim/URControl.log", "/ursim/polyscope.log", "/ursim/log_history.txt"]
FLIGHT_REPORTS = "/ursim/flightreports"
STATE_CHANNELS = ["robot_mode", "safety_mode", "speed_scaling"]

# Faults that stay invisible while the robot is idle: a URScript command sent 2 s after the
# injection forces the controller to act (and write), so that the consequences show.
PROBES = {"disk_full": "protective_stop()"}


def _docker(container: str, *cmd: str) -> str:
    """Run a command in the container and return its output (empty if it fails)."""
    out = subprocess.run(["docker", "exec", container, *cmd], capture_output=True, text=True, errors="replace", timeout=30)
    return out.stdout


def _sizes(container: str) -> dict[str, int]:
    """Size in bytes of each log file, to read only what the fault adds.

    Bytes rather than lines: the pendant log contains NUL characters, and PolyScope rewrites
    its log, so line counts are not reliable.
    """
    sizes = {}
    for path in LOG_FILES:
        out = _docker(container, "stat", "-c", "%s", path).strip()
        sizes[path] = int(out) if out.isdigit() else 0
    return sizes


def _transitions(t: np.ndarray, x: np.ndarray, t0: float) -> list[tuple[float, float]]:
    """Instants (relative to the injection, s) where a state channel changes value, with the new value."""
    if len(x) == 0:
        return []
    changes = [(float(t[0] - t0), float(x[0]))]
    for i in np.flatnonzero(np.diff(x) != 0) + 1:
        changes.append((round(float(t[i] - t0), 3), float(x[i])))
    return changes


def capture(fault: str, seconds: float, out_dir: Path) -> Path:
    """Provoke `fault`, record what URSim produces, repair it, and save the evidence.

    Returns:
        The path of the JSON evidence file.

    Raises:
        ValueError: if the fault is unknown.
    """
    if fault not in FAULTS:
        raise ValueError(f"unknown fault: {fault}")
    st = load_settings()
    faults = URSimFaults(st.robot_host, st.ursim_container)
    log.info("capture of %s: bringing the robot to RUNNING", fault)
    faults.wait_ready()
    connector = URSimConnector(st.robot_host, st.ursim_container)
    recorder = Recorder(connector, out_dir / "recordings").start()
    time.sleep(3)

    sizes = _sizes(st.ursim_container)
    reports_before = set(_docker(st.ursim_container, "ls", FLIGHT_REPORTS).split())
    injected_at = time.time()
    faults.inject(fault)
    if fault in PROBES:
        time.sleep(2)
        send_script(st.robot_host, PROBES[fault])
        time.sleep(seconds - 2)
    else:
        time.sleep(seconds)
    rec = recorder.window(injected_at - 2, time.time(), label=f"capture {fault}")
    rec.save(out_dir / "recordings")
    dashboard = {"robot_mode": _safe(connector.dashboard.robot_mode), "safety_status": _safe(connector.dashboard.safety_status)}

    new_lines = {}
    sizes_after = _sizes(st.ursim_container)
    for path, n in sizes.items():
        # A file smaller than before was rewritten: then it is read whole.
        start = n + 1 if sizes_after[path] >= n else 1
        text = _docker(st.ursim_container, "tail", "-c", f"+{start}", path)
        # Keep the lines written from 2 s before the injection on; a line without a time
        # (continuation of a multi-line message) follows the decision of the line above.
        kept, keep = [], True
        for line in text.splitlines():
            stamp = line_time(line.replace("\x00", ""))
            keep = stamp >= injected_at - 2 if stamp is not None else keep
            if keep and line.strip():
                kept.append(line)
        new_lines[path] = kept
    new_reports = sorted(set(_docker(st.ursim_container, "ls", FLIGHT_REPORTS).split()) - reports_before)
    black_boxes = [_read_flight_report(st.ursim_container, name, out_dir) for name in new_reports if name.endswith(".zip")]

    recorder.stop()
    revert_started = time.time()
    try:
        faults.revert(fault)
        mode_after = _safe(connector.dashboard.robot_mode)
    except TimeoutError as exc:
        mode_after = f"not recovered: {exc}"
    evidence: dict[str, Any] = {
        "fault": fault,
        "description": FAULTS[fault],
        "injected_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(injected_at)),
        "ursim_version": _docker(st.ursim_container, "cat", "/ursim/version.sh").strip(),
        "observed_seconds": seconds,
        "probe": f"{PROBES[fault]} sent 2 s after the injection" if fault in PROBES else None,
        "dashboard_after_fault": dashboard,
        "primary_fault_codes": [{**asdict(f), "t": round(f.t - injected_at, 3)} for f in rec.faults],
        "new_log_lines": new_lines,
        "rewritten_files": [p for p, n in sizes.items() if sizes_after[p] < n],
        "signals": {
            "recording_id": rec.id,
            "rate_hz": round(rec.rate_hz, 1),
            "channels": len(rec.channels),
            "stream_cuts_s": [(round(a + rec.t0 - injected_at, 3), round(b, 3)) for a, b in stream_gaps(rec)],
            "state_changes": {c: _transitions(rec.t, rec.channels[c], injected_at) for c in STATE_CHANNELS if c in rec.channels},
        },
        "stream_errors": dict(recorder.errors),
        "flight_reports": black_boxes,
        "recovery": {"seconds": round(time.time() - revert_started, 1), "robot_mode": mode_after},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{fault}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(injected_at))}.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    log.info("capture of %s saved: %s", fault, path)
    return path


def _safe(call: Any) -> str:
    """Result of a Dashboard call, or the error if the controller does not answer."""
    try:
        return call()
    except OSError as exc:
        return f"unreachable ({type(exc).__name__})"


def _read_flight_report(container: str, name: str, out_dir: Path) -> dict[str, Any]:
    """Copy a black box out of the container and keep its type, incident time and size."""
    local = out_dir / "flight_reports" / name
    local.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["docker", "cp", f"{container}:{FLIGHT_REPORTS}/{name}", str(local)], capture_output=True, timeout=60)
    if not local.exists():
        return {"name": name, "error": "could not be copied"}
    if not zipfile.is_zipfile(local):
        return {"name": name, "error": f"unreadable zip ({local.stat().st_size} bytes)", "path": str(local)}
    with zipfile.ZipFile(local) as z:
        meta = json.loads(z.read("metadata.json"))
        summary = z.read("summary.log").decode("utf-8", "replace")
    incident = next((line.split(":", 1)[1].strip() for line in summary.splitlines() if line.startswith("Incident Time")), None)
    return {"name": name, "type": meta.get("reportType"), "incident_time": incident, "path": str(local)}


def main() -> None:
    """Command line: capture one fault, or every fault with `all`."""
    parser = argparse.ArgumentParser(description="Capture the evidence of a fault provoked on URSim")
    parser.add_argument("fault", help="name of the fault, or 'all'")
    parser.add_argument("--seconds", type=float, default=15.0, help="observation time after the injection")
    parser.add_argument("--repeat", type=int, default=1, help="number of captures of each fault")
    args = parser.parse_args()
    st = load_settings()
    setup_logging(st)
    for run in range(1, args.repeat + 1):
        for fault in FAULTS if args.fault == "all" else [args.fault]:
            print(f"[{run}/{args.repeat}] {capture(fault, args.seconds, st.data_dir / 'catalogue')}", flush=True)


if __name__ == "__main__":
    main()
