# evaluation

Measures how good the system is: injects known faults, runs healthy periods, and scores what was detected and diagnosed.

**Will contain**

- Scenario runner, scores (detection rate, delay, confusion between faults, false alarms per hour, right cause top-1/top-3, cost).
- Reports are written to data/, never committed.

**Must not contain**

- The fault injections themselves (each machine provides its own).

**Depends on:** contracts, every engine module
