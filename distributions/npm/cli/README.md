# @agenthub/cli

AgentHub developer CLI — run verifier-gated agent missions from the
terminal:

```powershell
npm i -g @agenthub/cli
agenthub init
$env:AGENTHUB_CLI_MODEL_API_KEY = "<your key>"
agenthub run "write a pong.txt containing the word pong"
```

- **Zero runtime dependencies.** The `agenthub` command is a single
  frozen binary (installed per-platform via optionalDependencies);
  no Python, Node services, Docker, or PostgreSQL are required.
- **Verifier gate.** Mission success is confirmed by an independent
  verifier — the executing agent cannot self-certify. Exit codes:
  `0` SUCCEEDED, `1` FAILED, `2` CANCELLED, `3` wait timeout,
  `4` infrastructure error.
- **Local SQLite state.** Missions persist under `.agenthub/` in the
  workspace (`db/`, `data/`, `logs/`).

Subcommands: `init`, `run`, `exec` (headless, `--json`), `missions`,
`chat`, `tui`, `stacks`, `upgrade`.

Model channels: set `AGENTHUB_CLI_MODEL_API_KEY` (env-only, never
written to disk) plus optionally `AGENTHUB_CLI_PROVIDER` /
`AGENTHUB_CLI_MODEL` / `AGENTHUB_CLI_MODEL_BASE_URL`; without a key the
CLI falls back to the offline `mock` channel (no real API calls, no
fake success).

Supported platforms: **win32-x64**. The package fails with a clear
error on other platforms.

License: Apache-2.0. Project: <https://github.com/EVEDensity/AgentHub>
