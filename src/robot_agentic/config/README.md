# config

Loads the run-time settings (environment, files) once, with defaults and validation.

**Will contain**

- The settings type and its loading. Secrets are read here and never logged.

**Must not contain**

- Machine profiles (they live in each machine folder).

**Depends on:** nothing
