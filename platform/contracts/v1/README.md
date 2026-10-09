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

Workspace claim requests require a bounded `supportedWorkUnitKinds` capability
declaration. Mission Control applies it before candidate locking and leasing;
it is transient process capability and is never persisted as WorkUnit truth.
Mission-scoped claim requests are unchanged.

`workspace-work-unit-claim-request.schema.json` additionally defines optional
`supportedCapabilities` (legacy default empty). It describes a Runner's declared
operational support, never grants tools or replaces Contract authorization.
`mission-execution-status.schema.json` combines durable WorkUnit state with
expiry-bounded, exact-binding Runner contact. PENDING remains waiting when
contact is available; executing requires an active WorkUnit lease. Producers are
Mission Control workspace-claim admission and its read projection; consumers
are Runner polling clients and the frontend execution banner. The table and
rollback behavior are governed by
[ADR-0116](../../../docs/architecture/decisions/0116-runner-contact-and-chat-execution-status.md).

`mission-contract.schema.json` optionally carries immutable `governance` policy.
When omitted from a v1 document, Mission Control applies the v1 default of
86,400 seconds for human Decision response and serializes that resolved value.

New Mission projections include `contractVersion` so internal consumers can
resolve the exact immutable Contract revision. It remains optional in the v1
JSON Schema solely so previously emitted Mission documents remain valid.

`contract.lifecycle.revised` records the source Mission, previous and new
versions, and the human-supplied reason. The event creates no Mission rebind.

`execution-checkpoint.schema.json` defines the content-minimized durable
checkpoint projection. Tool/model content is intentionally excluded; terminal
shape and the server-generated state digest are part of the v1 contract.

The optional `resumeProtocolVersion`, `nextAction`, `idempotencyKey`,
`workspaceRevision`, and `contextManifestDigest` fields now match the domain
and repository projection. Older documents without them remain valid. An
included `nextAction` requires a protocol version and tool-call identity;
execution-scoped keys and fingerprints remain metadata, never a grant to
replay work. The Harness journal retains actual arguments/results, and strict
resume still validates lease/attempt, workspace/context, and ToolReceipts.
See ADR-0110 and `tests/contracts/test_execution_checkpoint_contract.py`.
