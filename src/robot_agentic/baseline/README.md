# baseline

Defines what "normal" is for one machine, recorded at a known-good moment, and compares new data with it.

**Will contain**

- Recording a baseline: standard tests on a healthy machine, normal log messages and rates.
- Comparison: what differs, by how much, and whether it matters.

**Must not contain**

- Running the tests themselves on the machine (the machine's actions do that).

**Depends on:** contracts, storage
