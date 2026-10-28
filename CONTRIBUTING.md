# Code principles

These rules apply to all the code of the repository, tests included. `ruff check` checks rules 1 and 2 automatically; the others are for review.

## 1. Type annotations everywhere

- Every function and method annotates all its parameters and its return value (`-> None` included), tests included.
- Class attributes and variables whose type is not obvious when reading are annotated.
- `Any` is reserved for truly dynamic data (JSON arguments sent by the LLM, API responses). Everywhere else, name the type.

## 2. Docstrings everywhere

- Every module, class, function and method has a docstring, public or private.
- The first line says **what the element does**. The rest explains what the signature does not say: the role in the system, units, formats, side effects, the errors raised and why.
- Use the `Args:`, `Returns:` and `Raises:` sections (Google style) as soon as a parameter or a return value is not obvious.

## 3. Deep modules (*A Philosophy of Software Design*, J. Ousterhout)

The goal: a **simple interface** hiding a **rich implementation**. This is what the book calls a "deep" module.

- **No small functions called only once** to "simplify". A long function that does one coherent thing reads from top to bottom and is debugged with one breakpoint; ten three-line functions make you jump around.
- Extract a function **only** if one of these is true:
  - it is reused in several places;
  - it hides real complexity behind a clearly simpler interface;
  - it must be tested on its own.
- **No methods or layers that only pass the call on** (*pass-through*): every layer must add something.
- **Each module hides one decision.** For example, the binary format of the UR packets only exists in `connectors/ursim.py`: the rest of the code only sees `Sample`s.
- **Define errors out of existence** when possible, by designing interfaces that make the error case impossible or normal. Otherwise, raise an explicit exception. Never a silent `except`.
- **Few, readable files.** Each file must be readable on its own, from top to bottom.

## 4. Logging

- Every module declares `log = logging.getLogger(__name__)`. `print` is reserved for the terminal interface (`hmi.py`) and command-line scripts.
- Levels:
  - `DEBUG`: details useful for debugging (packets, requests, values).
  - `INFO`: the course of events (connection, test started, alert, report, Claude call with tokens and cost).
  - `WARNING`: a recovered anomaly (stream cut then restarted, test refused by the safety checks).
  - `ERROR`: an operation that failed.
- Messages use deferred arguments: `log.info("test %s: %s", name, outcome)`. No f-strings in log messages.
- Never log a secret (API key, PIN code).
- Logs are written to `data/robot_agentic.log` (`DEBUG` level) and shown in the terminal from the `LOG_LEVEL` level (`WARNING` by default).

## 5. Comments

- Comment **only** what the code cannot say: the *why* of a choice, a unit, a format, an invariant, a reference to an external document, a trap.
- Do not paraphrase the code (`# increment i`). If a comment seems necessary to explain *what* a block does, first rephrase it in the function's docstring.

## 6. Conventions

- Everything is in English: identifiers, docstrings, comments, logs, prompts, texts for the user or the LLM.
- Before every commit: `ruff check` and `pytest` must pass.
