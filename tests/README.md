# Test Layout

Tests are organized by the contract they protect:

- `domain/`: pure state transitions and invariants.
- `persistence/`: transaction, event ledger, lease, and recovery behavior.
- `api/`: request validation, authorization, and response contracts.
- `contracts/`: cross-process and protocol compatibility.
- `compat/`: one-way legacy mappings.

New execution features should include a domain test first, then persistence and
API coverage as the blast radius requires. A successful response is not enough:
tests must verify honest failure, restart recovery, idempotency, and evidence
requirements where applicable.
