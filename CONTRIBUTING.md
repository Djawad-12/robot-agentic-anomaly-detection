# Contributing

## Git workflow

1. Every change starts from an issue of the GitHub Project, with its acceptance criteria.
2. One branch per issue, created from `prod`: `<type>/<issue>-<short-name>`, for example
   `feat/7-ursim-signals`.
3. One pull request per issue, into `prod`, with `Closes #<issue>` in its description.
4. A pull request is merged only after approval, with CI green and the acceptance criteria
   checked (results pasted in the issue).
5. Nothing is pushed directly to `prod`.

### Commit messages and pull request titles

```
<type>(<scope>): <what changed, in a few words>

- test <what is tested>
- implement <what is implemented>
- <any other change, one line each>
```

- `type`: `feat` (new feature), `fix` (bug fix), `refactor` (no behaviour change), `test`,
  `docs`, `chore` (tooling, structure, CI).
- `scope`: the folder or feature changed, for example `contracts`, `ursim`, `storage`.
- The body lists every change, one line each, starting with a verb.

Example:

```
feat(ursim): read the real-time signal stream

- test the parsing of a recorded real-time packet
- test the reconnection after a container restart
- implement the URSim signal source
```

## Code principles

`ruff check` checks rules 1 and 2 automatically; the others are for review.

### 1. Type annotations everywhere

- Every function and method annotates all its parameters and its return value (`-> None`
  included), tests included.
- Class attributes and variables whose type is not obvious when reading are annotated.
- `Any` is reserved for truly dynamic data (JSON arguments sent by the LLM, API responses).

### 2. Docstrings everywhere

- Every module, class, function and method has a docstring, public or private.
- The first line says what the element does. The rest explains what the signature does not
  say: role in the system, units, formats, side effects, errors raised and why.
- Use the `Args:`, `Returns:` and `Raises:` sections (Google style) when needed.

### 3. Deep modules (*A Philosophy of Software Design*, J. Ousterhout)

- A simple interface hiding a rich implementation.
- No small functions called only once, no layers that only pass the call on.
- Each module hides one decision. For example, the binary format of the UR packets only
  exists in `machines/ursim/`: the engine only sees the types of `contracts`.
- Define errors out of existence when possible; otherwise raise an explicit exception. Never
  a silent `except`.
- Few, readable files, each readable on its own from top to bottom.

### 4. Logging

- Every module declares `log = logging.getLogger(__name__)`. `print` is reserved for `apps`.
- `DEBUG` details, `INFO` the course of events, `WARNING` a recovered anomaly, `ERROR` a failed
  operation.
- Deferred arguments (`log.info("test %s: %s", name, outcome)`), no f-strings.
- Never log a secret (API key, PIN code).

### 5. Comments

Only what the code cannot say: the why of a choice, a unit, a format, an invariant, a
reference to an external document, a trap.

### 6. Conventions

- Everything is in English.
- Nothing generated is committed: no PDF, no run-time data, no evaluation report.
- Before every commit: `ruff check` and `pytest` pass.
