# Robot Agentic: software anomaly detection

Read and apply: @CONTRIBUTING.md (git workflow, commit format, code principles).
Project structure and dependency rule: @README.md

- Never push to `prod` directly: one branch and one pull request per issue, approved by the owner.
- The engine (`src/robot_agentic/`) never names a machine. Machine code lives in `machines/`.
- URSim runs in the Docker container `ursim`.
