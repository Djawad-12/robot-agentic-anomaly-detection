# contracts

The types and interfaces shared by the engine and every machine. Changing a contract changes every machine: these files move slowly and each change gets a decision note.

**Will contain**

- The data types: a sample (instant + named values with units), a log line, a fault code, an action description, an operator decision.
- Small capability interfaces a machine implements only if it has them: signal source, log source, fault-code source, documentation, actions, black box, emergency stop.
- The machine profile format: channels, units, expected rates, limits, pairs to compare.

**Must not contain**

- Any logic beyond the data itself, any machine name, any third-party dependency.

**Depends on:** nothing
