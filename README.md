# Robot Agentic

Software anomaly detection and diagnosis for industrial machines. The engine is the same for
every machine: a machine is plugged in by implementing the contracts in
`src/robot_agentic/contracts/`. URSim (a simulated Universal Robots arm) is the first machine.

## Use cases

| Use case | Trigger | Output |
|---|---|---|
| **Watch** | Always running | Alerts, each with a frozen evidence bundle |
| **Diagnose** | An alert, or an operator complaint | A report: root cause, evidence, alternatives ruled out, repair steps |
| **Check-up** | Commissioning, a schedule, or after a repair | A baseline, or a comparison with it |

## Structure

```
.github/                  issue and pull request templates, CI
docs/
  decisions/              one note per architecture decision, with its reasons
  use-cases/              the specification of each use case
src/robot_agentic/        the generic engine: no machine name ever appears here
  contracts/              data types and capability interfaces every machine implements
  acquisition/            reads a machine's sources continuously, survives disconnections
  storage/                keeps samples and events on disk, answers time-window queries
  detection/              rules that raise alerts, driven by the machine profile
  baseline/               what "normal" means for a machine, and comparison with it
  diagnosis/              the investigation: evidence, hypotheses, checks, report
  safety/                 limits and operator approval for every action on the machine
  knowledge/              search in the machine's documentation
  evaluation/             scores the system on injected faults and on healthy runs
  apps/                   entry points of the three use cases
  config/                 loading of the run-time settings
machines/                 one folder per machine, implementing the contracts
  fake/                   reference machine for the tests
  ursim/                  Universal Robots simulator
tests/
  contract/               the suite every machine must pass
  unit/                   tests of the engine with the fake machine
  integration/            tests against a running machine (URSim)
config/                   example settings
```

## Dependency rule

```
apps ──> engine modules ──> contracts <── machines
```

- `contracts` depends on nothing.
- The engine modules depend on `contracts` only, never on a machine.
- A machine depends on `contracts` only, never on the engine.
- `apps` connects a machine to the engine.

## Workflow

Every change goes through a pull request into `prod`, reviewed and approved before merge.
See [CONTRIBUTING.md](CONTRIBUTING.md).
