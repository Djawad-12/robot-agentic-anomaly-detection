# detection

Deterministic rules that turn data into alerts, with no LLM. Rules are generic; their thresholds and channels come from the machine profile.

**Will contain**

- Stream health (rate, gaps), log and fault-code rules, target-vs-actual, comparison between similar parts, limits.
- An alert type: what was seen, when, which rule, and the evidence bundle to freeze.

**Must not contain**

- Thresholds written in the code, machine names.

**Depends on:** contracts, storage, baseline
