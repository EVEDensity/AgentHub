# AgentHub public contracts v1

This directory is the language-neutral contract boundary shared by the
control plane, runners, adapters, and user interfaces.

## Compatibility rules

- Files in `v1` use JSON Schema Draft 2020-12.
- Existing required fields and enum values are never removed within v1.
- Additive optional fields are allowed after the contract tests pass.
- Breaking changes require a new version directory.
- Event payloads contain references to large artifacts, never artifact data.
- The event catalog is authoritative for event names and aggregate ownership.

Domain objects use camelCase to match the public API. Event envelopes use
snake_case because they are persisted and transported as ledger records.
