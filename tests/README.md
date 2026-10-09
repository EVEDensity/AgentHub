# Test Layout

Tests are organized by the contract they protect:

- `domain/`: pure state transitions and invariants.
- `persistence/`: transaction, event ledger, lease, and recovery behavior.
- `api/`: request validation, authorization, and response contracts.
- `services/`: storage adapters and application-service boundary behavior.
- `integration/`: cross-process HTTP composition and opt-in infrastructure
  contracts; infrastructure-backed tests must skip explicitly when unavailable.
- `contracts/`: cross-process and protocol compatibility.
- `compat/`: one-way legacy mappings.

New execution features should include a domain test first, then persistence and
API coverage as the blast radius requires. A successful response is not enough:
tests must verify honest failure, restart recovery, idempotency, and evidence
requirements where applicable.

`api/mission_app_fixture.py` owns shared in-process Mission HTTP dependency
overrides, including the fake Runner presence recorder. It loads the existing
fake adapters lazily so `test_missions_api.py` can preserve its historical
`build_app` imports without a circular module dependency. These fixtures isolate
HTTP tests from the developer database; only infrastructure-backed integration
tests may claim real database behavior.

`services/runner_checkpoint_fixture.py` builds exact checkpoint acknowledgement
DTOs for non-persistent Runner composition controls. It echoes resume metadata
without establishing journal authority; restart acceptance uses real persistence
and private journals in `integration/test_recovery_process.py`.

For the root Python suite, install the versioned development dependencies before
running tests:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```
