# Project summary and decisions

This file records every decision taken with the project owner (October 2026). When the code
and this file disagree, this file wins until it is updated.

## 1. What the project is

An AI agent that finds the **root cause** of a machine failure and the repair to do.

There are two independent ways of detecting anomalies. They never mix in one investigation.

| | Software anomaly detection | Physical anomaly detection |
|---|---|---|
| Data | What the robot controller reports: signals (joint positions, currents, temperatures…), logs, fault codes, documentation | What our own sensors measure: microphone + amplifier, MPU-6050 accelerometer, current clamp (SCT-013 + ADS1115), INA226 |
| Works on | Any machine whose software we can access | Any machine, even a closed one |
| Test bench | URSim (official Universal Robots simulator), then the RoArm-M2-S | A fan with loosened screws, then the RoArm-M2-S with loosened screws |
| Status | **Current work** | **Later** (see section 6) |

**Current scope: software anomaly detection on URSim only.** Everything related to the
RoArm-M2-S and to physical anomaly detection is removed from the code until the software part
is satisfying. Files must stay small enough to be read one by one.

## 2. Code decisions

- Everything in English: identifiers, docstrings, comments, logs, prompts, terminal text, reports.
- Docstrings stay on every module, class, function and method (CONTRIBUTING.md rule 2).
- LangGraph stays for the investigation flow.
- No `HMI` Protocol: the terminal interface is used directly.
- The URSim connector is a single file.
- One entry point for checks: `robot-agentic health` (replaces `tools/healthcheck.py`).
- No baselines (reference recordings): removed completely. Detection relies only on logs,
  fault codes and signal values.
- Events are plain dataclasses (no pydantic); the recorder keeps signals in a numpy ring
  buffer, so that it fits in a Raspberry Pi's memory.
- Detection thresholds live in a YAML file: `config/detection.yaml`.

## 3. Detection rules (software)

Alerts from logs and fault codes, plus rules on the signals:

| | Rule | Example | Role |
|---|---|---|---|
| b | Controller target vs reality | `q_target` vs `q_actual` (following error); expected torque vs measured current | **Main** |
| e | Joint comparison by effort ratio | measured current ÷ expected torque, compared between joints | **Main** |
| a | Hardware limits | motor temperature, current, voltage above the manual's limits | Backup |
| c | Comparison with the recent past | current 30 % above its own average; window configurable from 10 min to 1 day | Backup |

Other ideas, documented but not implemented: (d) comparing repeated production cycles,
(f) Claude reading a summary at regular intervals.

Fault codes orient the investigation (e.g. C204 = joint position deviation → the agent starts
with rule b on that joint).

## 4. Investigation behaviour

- Loop mode: the operator is asked before every root-cause analysis.
- The operator approves **every** robot movement (PIN, if configured, asked once per investigation).
- All five robot tests stay available: `joint_test`, `move_hand`, `add_load`, `overall_test`,
  `recover_robot`. `add_load` must always restore the original payload.
- After each test Claude receives a plot and the full table of values (budget ~40,000 tokens).
- Model: Claude Opus 5.5, effort `high`. Spending is acceptable during testing.
- Benchmarks are graded by keywords **and** a Claude judge.

## 5. URSim fault catalogue (new deliverable)

A landscape LaTeX PDF, in English, made of one table: every fault we can create on URSim.

- Columns: how to provoke / how to revert · logs and fault codes (with their official
  meaning) · effect on the signals · category · sources and proof.
- **Proof for every row**: a link to the official Universal Robots documentation **and** the
  exact log lines / fault codes observed when the fault was really provoked on our URSim
  (date and command). Anything not verified is marked "not verified". No guessing.
- Faults: wrong payload, invalid URScript, protective stop, controller process killed,
  controller frozen, plus: kill one service, network faults (delay, loss, blocked port),
  CPU overload, disk full.
- The catalogue is the dataset for the benchmark statistics: % detected, % correct root
  cause, detection time, cost per investigation.

## 6. Later (not in the code for now)

**RoArm-M2-S (software detection):** it reports joint positions and loads over USB but has no
real logs or fault codes. Our connector will generate its own log lines and error codes.

**Physical anomaly detection:**
- The ESP32 reads the sensors and sends **raw samples** over **USB** to the Raspberry Pi; the
  Pi computes everything (FFT, rules).
- Python rules raise the alert; Claude then reads the plots/spectra and explains the cause.
- Fan: not bought yet. An AC (mains) fan is measured with the SCT-013 clamp, a DC fan with the INA226.
- To decide then: microphone sample rate, how to test without hardware.

**Deployment:** first test everything on URSim on the laptop, build the benchmark statistics,
then move the code to the Raspberry Pi.

## 7. Work plan

| Fault catalogue | Code |
|---|---|
| 1. Study URSim: processes, log files, fault codes, official documentation | 1. Refactor: English, no baselines, no RoArm/physical code, redundancy removed, known bugs fixed |
| 2. Provoke each fault on URSim and capture the evidence | 2. Rules b + e, then a + c, thresholds in YAML |
| 3. Write the PDF | 3. New faults in the injection script and the benchmarks |
| 4. Benchmark statistics on this dataset | 4. Detection ideas document |

One commit per finished step, on the branch `refactor/software-detection`.
