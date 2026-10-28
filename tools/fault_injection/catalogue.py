"""Builds the URSim fault catalogue (landscape LaTeX PDF, one table) from the captures.

Two inputs, kept apart so that nothing observed is ever written by hand:
- `catalogue.yaml`: category, commands, official sources and notes, written by hand;
- `data/catalogue/*.json`: the evidence saved by `capture.py`. Everything in the "observed"
  columns is computed from these files: fault codes and log lines with the number of runs
  where they appeared, signal cuts and rates, state changes, black boxes, recovery time.
The official meaning of each observed fault code is read from the error code pages
downloaded by `tools.fetch_ur_docs`.

Usage: python -m tools.fault_injection.catalogue
Writes docs/catalogue/ursim_fault_catalogue.tex and compiles it with latexmk.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from robot_agentic.config import load_settings
from robot_agentic.connectors.ursim import parse_log_line
from robot_agentic.core.events import Level
from robot_agentic.core.recorder import Recording

from .inject import FAULTS

HERE = Path(__file__).parent
OUT = HERE.parent.parent / "docs" / "catalogue"
ROBOT_MODES = {0: "DISCONNECTED", 1: "CONFIRM_SAFETY", 2: "BOOTING", 3: "POWER_OFF", 4: "POWER_ON", 5: "IDLE", 7: "RUNNING"}
SAFETY_VALUES = {1: "NORMAL", 2: "REDUCED", 3: "PROTECTIVE_STOP"}
# Lines that are not evidence of the fault: our own connections, stack-trace frames.
NOISE = re.compile(r'Socket "|TCPServer: Accepted|TCPReceiver closed|object was destroyed|MultiTCPSender send|^\s*at |^\.\.\. \d+ more')


def tex(text: Any) -> str:
    """Escape a text for LaTeX."""
    out = str(text)
    for a, b in [("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"), ("#", r"\#"), ("_", r"\_")]:
        out = out.replace(a, b)
    for a, b in [("{", r"\{"), ("}", r"\}"), ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}")]:
        out = out.replace(a, b)
    out = out.replace(r"\textbackslash\{\}", r"\textbackslash{}").replace("±", r"$\pm$").replace("→", r"$\rightarrow$")
    # Long identifiers (get_actual_joint_positions) may break after an underscore or a comma.
    return out.replace(r"\_", r"\_\allowbreak{}").replace(",", r",\allowbreak{}")


def official_codes(manual: Path) -> dict[str, tuple[str, str]]:
    """Official title and page of each code and sub-code, from the downloaded error code pages."""
    codes: dict[str, tuple[str, str]] = {}
    if not manual.exists():
        return codes
    for section in manual.read_text().split("\n## ")[1:]:
        lines = section.splitlines()
        title, url = lines[0], next((x.removeprefix("Source: ") for x in lines if x.startswith("Source: ")), "")
        codes[title.split()[0]] = (title, url)
        for line in lines:
            if re.match(r"C\d+A\d+ ", line):
                codes[line.split()[0]] = (line, url)
    return codes


def effective_rate(evidence: dict[str, Any], recordings: Path) -> float:
    """Samples received per second over the whole observed window, in Hz.

    The rate saved by the capture is measured between the first and the last sample: for a
    stream that stops for good it still looks normal. Dividing by the window length shows
    the stream as it was received.
    """
    rec = Recording.load(recordings / f"{evidence['signals']['recording_id']}.json.gz")
    return len(rec.t) / rec.duration if rec.duration else 0.0


def summarise(runs: list[dict[str, Any]], recordings: Path) -> dict[str, Any]:
    """What a fault produced across its captures, each fact with the number of runs that showed it."""
    n = len(runs)
    codes: Counter[str] = Counter()
    code_delay: dict[str, list[float]] = {}
    for r in runs:
        for code in {c["code"] for c in r["primary_fault_codes"]}:
            codes[code] += 1
            code_delay.setdefault(code, []).append(min(c["t"] for c in r["primary_fault_codes"] if c["code"] == code))

    lines: Counter[tuple[str, str, str]] = Counter()
    for r in runs:
        seen = set()
        for raw_lines in r["new_log_lines"].values():
            previous = None
            for raw in raw_lines:
                entry = parse_log_line(raw, previous)
                if entry is None:
                    continue
                previous = entry
                if NOISE.search(entry.text) or entry.level == Level.DEBUG:
                    continue
                # Standalone numbers (line numbers, addresses, times) vary between runs; numbers
                # inside a word (fault codes such as C209A0) are kept.
                key = (entry.source, entry.level.value, re.sub(r"(?<![A-Za-z\d])\d+(\.\d+)*(?![A-Za-z\d])", "#", entry.text)[:140])
                seen.add(key)
        lines.update(seen)
    order = {"FATAL": 0, "ERROR": 1, "WARNING": 2, "INFO": 3}
    # A line is kept when it appeared in most runs (3 of 5): a line seen once may be a coincidence.
    common = sorted((k for k, c in lines.items() if c >= (n + 1) // 2), key=lambda k: (order.get(k[1], 4), -lines[k]))

    def median(values: list[float]) -> float:
        """Median of a list (0 if empty)."""
        return statistics.median(values) if values else 0.0

    cuts = [r["signals"]["stream_cuts_s"] for r in runs]
    states: Counter[str] = Counter()
    for r in runs:
        for channel, names in (("robot_mode", ROBOT_MODES), ("safety_mode", SAFETY_VALUES)):
            changes = [v for t, v in r["signals"]["state_changes"].get(channel, []) if t > 0]
            if changes:
                path = " → ".join(names.get(int(v), str(int(v))) for v in changes)
                states[f"{channel}: {path}"] += 1
    boxes = Counter(b.get("type") or b.get("error", "?") for r in runs for b in r["flight_reports"])
    dashboard = Counter(f"{r['dashboard_after_fault']['robot_mode']} / {r['dashboard_after_fault']['safety_status']}" for r in runs)
    errors = Counter(s for r in runs for s in r["stream_errors"])
    return {
        "n": n,
        "codes": [(c, codes[c], median(code_delay[c])) for c in sorted(codes, key=lambda c: -codes[c])],
        "lines": [(k, lines[k]) for k in common[:7]],
        "cut_runs": sum(1 for c in cuts if c),
        "cut_count": median([len(c) for c in cuts]),
        "cut_total": median([sum(d for _, d in c) for c in cuts]),
        "rate": median([effective_rate(r, recordings) for r in runs]),
        "states": states.most_common(),
        "boxes": boxes.most_common(),
        "box_runs": sum(1 for r in runs if r["flight_reports"]),
        "dashboard": dashboard.most_common(1)[0][0] if dashboard else "",
        "stream_errors": errors.most_common(),
        "recovery": median([r["recovery"]["seconds"] for r in runs]),
        "recovered": sum(1 for r in runs if r["recovery"]["robot_mode"] == "RUNNING"),
        "dates": sorted(r["injected_at"] for r in runs),
        "probe": runs[0].get("probe"),
    }


def row(fault: str, curated: dict[str, Any], s: dict[str, Any], official: dict[str, tuple[str, str]]) -> str:
    """One row of the table, as LaTeX."""
    n = s["n"]
    observed = []
    for code, count, delay in s["codes"]:
        meaning = official.get(code) or official.get(re.sub(r"A\d+$", "", code))
        caused = rf" --- \textit{{caused by the probe: {tex(s['probe'])}}}" if s["probe"] else ""
        observed.append(rf"\textbf{{{tex(code)}}} ({count}/{n} runs, after {delay * 1000:.0f} ms){caused}")
        if meaning:
            observed.append(rf"\textit{{Official: {tex(meaning[0])}}}")
    if not s["codes"]:
        observed.append(r"\textbf{No fault code}")
    for (source, level, text), count in s["lines"]:
        observed.append(rf"\textsf{{{tex(source or 'unknown')}}} {tex(level)} ({count}/{n}): \texttt{{{tex(text)}}}")
    if not s["lines"]:
        observed.append("No log line (besides connection noise).")

    signals = [f"Samples received: {s['rate']:.0f} per second over the 17 s window (median; 500 by design)."]
    if s["cut_runs"]:
        signals.append(f"Stream cuts in {s['cut_runs']}/{n} runs: {s['cut_count']:.0f} cut(s), {s['cut_total']:.1f} s in total (median).")
    else:
        signals.append(f"No stream cut (0/{n}).")
    signals += [f"{tex(state)} ({count}/{n})" for state, count in s["states"]] or ["No change of robot or safety mode."]
    signals.append(f"Dashboard after 15 s: {tex(s['dashboard'])}.")
    signals.append(
        f"Black box: {s['box_runs']}/{n} runs" + (f" ({', '.join(f'{tex(t)} ×{c}' for t, c in s['boxes'])})" if s["boxes"] else "") + "."
    )
    if s["stream_errors"]:
        signals.append("Our streams in error at the end: " + ", ".join(f"{tex(k)} ({c}/{n})" for k, c in s["stream_errors"]) + ".")
    signals.append(f"Back to RUNNING: {s['recovered']}/{n}, {s['recovery']:.1f} s (median).")

    proof = [rf"\textbf{{{tex(curated['category'])}}}"]
    proof += [rf"\url{{{src['url']}}}: {tex(src['supports'])}" for src in curated.get("sources", [])]
    seen_codes = {official.get(c, official.get(re.sub(r"A\d+$", "", c), ("", "")))[1] for c, _, _ in s["codes"]} - {""}
    proof += [rf"\url{{{url}}}: official page of the observed code." for url in sorted(seen_codes)]
    proof.append(
        rf"Evidence: \texttt{{data/catalogue/{tex(fault)}-*.json}} ({n} runs, {tex(s['dates'][0])} to {tex(s['dates'][-1][11:])})."
    )
    proof += [rf"\textit{{Note: {tex(x)}}}" for x in curated.get("notes", [])]

    cells = [
        rf"\textbf{{{tex(fault)}}}\newline {tex(FAULTS[fault])}",
        rf"\textit{{Provoke:}} {tex(curated['provoke'])}\newline\textit{{Revert:}} {tex(curated['revert'])}",
        r"\newline ".join(observed),
        r"\newline ".join(signals),
        r"\newline ".join(proof),
    ]
    return " & ".join(cells) + r" \\ \midrule"


def main() -> None:
    """Read the captures and the hand-written part, write the LaTeX file and compile it."""
    settings = load_settings()
    curated = yaml.safe_load((HERE / "catalogue.yaml").read_text())
    official = official_codes(settings.manuals_dir / "ursim" / "ur_error_codes_SW5_26.md")
    captures: dict[str, list[dict[str, Any]]] = {}
    for path in sorted((settings.data_dir / "catalogue").glob("*.json")):
        evidence = json.loads(path.read_text())
        captures.setdefault(evidence["fault"], []).append(evidence)
    summaries = {f: summarise(captures[f], settings.data_dir / "catalogue" / "recordings") for f in FAULTS if f in captures}
    version = next(iter(captures.values()))[0]["ursim_version"].removeprefix("VERSION=") if captures else "?"

    overview = []
    for f, s in summaries.items():
        has_log = any(level in ("ERROR", "WARNING", "FATAL") for (_, level, _), _ in s["lines"])
        marks = [bool(s["codes"]), has_log, bool(s["cut_runs"] or s["states"] or s["rate"] < 450), bool(s["box_runs"])]
        cells = [r"\yes" if m else r"\no" for m in marks]
        if s["probe"]:  # the code and the black box come from the probe, not from the fault itself
            cells[0] = r"\no\ (probe: \yes)" if s["codes"] else cells[0]
            if not s["cut_runs"] and s["rate"] >= 450:  # only the probe's change of safety mode
                cells[2] = r"\no\ (probe: \yes)" if s["states"] else cells[2]
            cells[3] = r"\no\ (probe: unreadable)" if s["box_runs"] else cells[3]
        overview.append(rf"\texttt{{{tex(f)}}} & {tex(curated[f]['category'])} & " + " & ".join(cells) + r" \\")

    body = (HERE / "catalogue_template.tex").read_text()
    body = body.replace("@VERSION@", tex(version)).replace("@DATE@", time.strftime("%Y-%m-%d"))
    body = body.replace("@RUNS@", str(max((s["n"] for s in summaries.values()), default=0)))
    body = body.replace("@OVERVIEW@", "\n".join(overview))
    body = body.replace("@ROWS@", "\n".join(row(f, curated[f], s, official) for f, s in summaries.items()))
    OUT.mkdir(parents=True, exist_ok=True)
    tex_path = OUT / "ursim_fault_catalogue.tex"
    tex_path.write_text(body)
    subprocess.run(["latexmk", "-pdf", "-interaction=nonstopmode", "-quiet", tex_path.name], cwd=OUT, check=True, capture_output=True)
    print(f"{len(summaries)} faults, {sum(s['n'] for s in summaries.values())} captures -> {tex_path.with_suffix('.pdf')}")


if __name__ == "__main__":
    main()
