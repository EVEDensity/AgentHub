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

`work-unit-claim-response.schema.json` is the additive Runner polling contract.
`claimStatus` distinguishes ready-work absence from tenant capacity saturation;
the existing `workUnit` field remains unchanged for backward-compatible
consumers.

`mission-contract.schema.json` optionally carries immutable `governance` policy.
When omitted from a v1 document, Mission Control applies the v1 default of
86,400 seconds for human Decision response and serializes that resolved value.

New Mission projections include `contractVersion` so internal consumers can
resolve the exact immutable Contract revision. It remains optional in the v1
JSON Schema solely so previously emitted Mission documents remain valid.

`contract.lifecycle.revised` records the source Mission, previous and new
versions, and the human-supplied reason. The event creates no Mission rebind.
