# Detection ideas (software anomaly detection)

How the agent can notice that something is wrong, beyond the alerts already raised by the
log and the fault codes (`core/checks.py`, `EventWatcher`). Decisions are in
[summary.md](../summary.md), section 3: **b** and **e** are the main rules, **a** and **c** the
backups, **d** and **f** are documented only. Section 2 adds what the URSim fault catalogue
taught us.

All thresholds will live in `config/detection.yaml`, with their unit, so that they can be
changed without touching the code.

## 1. The six ideas

| | Idea | What is compared | Needs a reference of the healthy robot? | Catches | Status |
|---|---|---|---|---|---|
| a | Hardware limits | each value against the limit of the manual (motor temperature, joint current, voltages) | no | gross failures only | backup |
| b | Target vs reality | what the controller asked for against what happened: `q_target` vs `q_actual` (following error), expected current vs measured current | no: the controller gives the expected value itself | friction, blocked joint, wrong payload | **main** |
| c | Recent past | a value against its own average over a sliding window (10 min to 1 day, configurable) | learned automatically, no recording step | drift, sudden change | backup |
| d | Repeated cycles | one production cycle against the previous ones | learned automatically | slow wear | documented only (no production cycle on URSim) |
| e | Joint comparison | effort ratio (measured current ÷ expected current) of each joint against the others | no | one abnormal joint | **main** |
| f | Claude at regular intervals | Claude reads a compact summary every N minutes | no | subtle patterns | documented only (cost: about $0.04 per look on Opus) |

### b. Target vs reality

- **Following error:** `|q_target − q_actual|` per joint. A healthy joint follows its target
  closely; a growing gap means the joint cannot keep up.
- **Current:** the controller computes the current it expects for each joint. The black boxes
  contain it (`joint_N_current_target`). The live Real-Time stream used today does not read it
  yet: the UR documentation gives its position in the packet (to verify on URSim before use).
- Alert when the gap stays above a threshold for longer than a minimum time, so that one
  noisy sample does not raise an alert.

### e. Joint comparison by effort ratio

Raw currents cannot be compared between joints: the shoulder lifts the whole arm, a wrist
almost nothing. The ratio `measured current ÷ expected current` removes the load: healthy
joints have similar ratios, a joint with friction has a higher one. Alert when one joint's
ratio is far from the others' (e.g. more than N times the spread of the others).

### a. Hardware limits

Values to take from the official documentation only (with the page as source), never
guessed: motor temperature, joint currents, voltages, speeds.

### c. Comparison with the recent past

Mean and spread of each channel over a sliding window, kept as summaries (one per minute),
not as raw samples: one day at 500 Hz would not fit in memory. Alert when a value leaves its
own usual range.

## 2. What the URSim fault catalogue showed

Figures from the 13 faults × 5 runs of `docs/catalogue/ursim_fault_catalogue.pdf` (medians;
"samples/s" = samples received per second over the 17 s observation window, 500 by design,
481 when nothing disturbs the stream).

- **Only the protective stop produced an official fault code** (C209A0, 5/5 runs, 24 ms).
- **4 faults leave no warning or error in any log:** wrong payload, controller frozen, CPU
  starvation, CPU overload. Three of them are visible only in the signal stream; wrong payload
  is invisible while the robot is idle.

| What the signal stream does | Faults (samples/s) |
|---|---|
| Stops for good | controller killed (58), network cut (56), port 30003 blocked (60) |
| Stops for a few seconds | controller frozen (227, one cut of 9 s) |
| Slower, with many short cuts | CPU starvation (153), network delay (71), packet loss (84) |
| Slower, without any cut | CPU overload (402) |
| Unchanged | wrong payload, invalid program, PolyScope frozen, disk full, protective stop |

Faults with an unchanged stream are seen through the logs (invalid program: `Syntax error`;
PolyScope frozen: `Failed to send state to client`; disk full: `java.io.IOException: No space
left on device`, only once the controller has to write) or not at all (wrong payload).

This suggests one more rule, close to idea a but on the stream rather than on a value:

| | Idea | What is compared | Status |
|---|---|---|---|
| g | Stream health | samples received per second against the design value (500 Hz on e-Series, "not guaranteed" by the UR documentation), and the time since the last sample | **proposed**, to be decided |

Wrong payload is the case for rule b: it needs the robot to move, then the measured current no
longer matches the expected one (not verified yet).
