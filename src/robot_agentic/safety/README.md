# safety

Checks every action before it reaches the machine: limits, argument bounds, operator approval. The only path from the engine to a machine action.

**Will contain**

- Validation of actions against their declared bounds, approval by the operator, a log of every decision.

**Must not contain**

- The actions themselves (they belong to the machine).

**Depends on:** contracts
