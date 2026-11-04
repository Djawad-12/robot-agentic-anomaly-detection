# machines

One folder per machine. A machine implements the capability interfaces of src/robot_agentic/contracts and nothing else; it never imports the engine.

**Will contain**

- fake/: a scripted machine for the tests.
- ursim/: the Universal Robots simulator.
- Each machine: its adapter code, its profile (data), its fault injections.

**Must not contain**

- Generic logic: if two machines need it, it belongs in the engine.

**Depends on:** contracts
