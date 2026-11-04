# machines/ursim

The Universal Robots simulator (URSim 5.26, UR3e) running in Docker.

**Will contain**

- adapter/: the code reaching URSim (real-time and primary ports, dashboard, log files, black boxes, Docker).
- profile/: channels, units, rates, limits, prompt section, documentation hints (data, no code).
- faults/: the fault injections used by evaluation.

**Must not contain**

- Generic logic.

**Depends on:** contracts
