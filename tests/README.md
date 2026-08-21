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

For the root Python suite, install the versioned development dependencies before
running tests:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```
