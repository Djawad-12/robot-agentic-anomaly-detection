# acquisition

Reads every source of a machine continuously, puts all items on one clock and hands them to storage. Hides threads, reconnections and clock offsets.

**Will contain**

- One reader per capability the machine offers, restarted when a source is lost.
- Records of every interruption (when, how long, why): a lost stream is itself a clue.

**Must not contain**

- Storage format, detection logic, machine-specific parsing.

**Depends on:** contracts, storage
