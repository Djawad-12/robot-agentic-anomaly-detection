# storage

Keeps samples and events on disk for a configurable period and answers "everything between t0 and t1". Replaces the in-memory recorder of the prototype.

**Will contain**

- Writing in segments, retention, time-window queries returning one aligned bundle (signals + logs + fault codes).
- Named, frozen bundles (an alert's evidence, a test, a baseline) that retention never deletes.

**Must not contain**

- Threads that read the machine (acquisition), interpretation of the values.

**Depends on:** contracts
