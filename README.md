# Robot Agentic — software anomaly detection

An AI agent that finds the **root cause** of a robot failure from the robot's four data sources:
**signals**, **log**, **fault codes** and **documentation**. It states hypotheses, checks them with
small supervised tests on the robot, then hands in a report: the cause, the evidence, the repair.

Current scope: **URSim**, the official Universal Robots simulator (UR3e). Every decision and the
work plan are in [summary.md](summary.md).

## Layout

```
src/robot_agentic/
├── __main__.py            command line: health, check, loop
├── config.py              settings (.env) and logging
├── hmi.py                 terminal interface
├── docs.py                documentation indexing and search (BM25)
├── core/                  independent of the machine
│   ├── events.py          shared types: Sample, LogLine, FaultCode, Alert, ToolSpec, Param
│   ├── interfaces.py      Connector contract: signals(), logs(), faults(), docs(), tools()
│   ├── recorder.py        reads the streams continuously, records each test (before, during, after)
│   ├── checks.py          deterministic alerts (log, fault codes) and stream cuts
│   └── safety.py          bounds of the tests, operator approval for every movement, PIN
├── agent/
│   ├── graph.py           LangGraph graph (watch → approval → investigate → report) and the two modes
│   ├── tools.py           the agent's tools (common ones + the connector's)
│   ├── context.py         what the LLM receives: plots and full values within a token budget
│   ├── llm.py             Claude calls, prompt caching, cost and call log
│   └── prompts/system.md  the agent's instructions
└── connectors/
    ├── ursim.py           URSim: Real-Time (30003), Primary (30001), URScript (30002), Dashboard (29999), log
    └── fake.py            machine simulated in Python, for the tests
tools/fault_injection/     faults provoked on URSim + expected scenarios
benchmarks/run.py          correct diagnoses (keywords + Claude judge), time, cost
tests/                     automatic tests (fake connector, fake LLM: no network, no cost)
```

## What the agent receives after a test

1. a **summary**: outcome, signal stream cuts, fault codes and log warnings during the test;
2. a **plot** of the channels that moved the most: LLMs spot an anomaly better on an image than
   in a list of numbers ([Zhou & Yu, ICLR 2025](https://arxiv.org/abs/2410.05440));
3. the **full values**, as a table, at the finest rate the `RECORDING_TOKEN_BUDGET` allows. The
   `get_recording` tool gives one channel back at full resolution (500 Hz).

## Installation

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env        # then fill in ANTHROPIC_API_KEY
```

URSim (Docker):

```bash
docker run -d --name ursim -e ROBOT_MODEL=UR3 \
  -p 5900:5900 -p 6080:6080 -p 29999:29999 -p 30001-30004:30001-30004 \
  universalrobots/ursim_e-series
# PolyScope interface: http://localhost:6080/vnc.html
# later: docker start ursim
```

Documentation: put the manuals (PDF) in `manuals/ursim/`, e.g. the Universal Robots *Service
Manual* and the error code list. They are not versioned.

## Usage

```bash
.venv/bin/robot-agentic health --api               # is everything ready?
.venv/bin/robot-agentic check                      # spot check
.venv/bin/robot-agentic check "the arm struggles when lifting"
.venv/bin/robot-agentic loop                       # continuous monitoring
```

`--yes` approves every test automatically: keep it for the simulator. Otherwise every movement
is asked to the operator (and `OPERATOR_PIN` once per investigation, if set).

### Fault injection and benchmarks

```bash
.venv/bin/python -m tools.fault_injection.inject list
.venv/bin/python -m tools.fault_injection.inject wrong_payload
.venv/bin/python -m tools.fault_injection.inject wrong_payload --revert
.venv/bin/python -m benchmarks.run                 # every scenario (paid calls)
```

### Tests

```bash
.venv/bin/python -m pytest
```

## Data produced

In `data/` (not versioned): recordings (`recordings/`), reports (`reports/`), log of the Claude
calls with tokens and cost (`llm_calls.jsonl`), full application log (`robot_agentic.log`).

## Later

The RoArm-M2-S connector and physical anomaly detection (our own sensors on the machine) come
once this software part is satisfying: see [summary.md](summary.md), section 6.
