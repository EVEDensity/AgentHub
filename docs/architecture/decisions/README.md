# Architecture Decision Records

ADRs record decisions that are expensive to reverse or affect more than one
module. Use four-digit sequence numbers and a short kebab-case title, for
example `0001-mission-control-is-source-of-truth.md`.

States are `proposed`, `accepted`, `superseded`, and `rejected`. Accepted ADRs
are immutable except for status and links; a new decision supersedes an old one.

Create new records from `0000-template.md`.

## Recent decisions

| ADR | Title |
|---|---|
| [0131](0131-registered-log-artifact-snapshots.md) | Display registered log Artifact snapshots through xterm |
| [0132](0132-desktop-verification-evidence-scopes.md) | Separate desktop browser preview from packaged and native acceptance |
| [0130](0130-native-session-mission-observation.md) | Recover native Session Missions as bounded observations |
| [0129](0129-mission-assistant-ui-projection.md) | Bind the shared assistant dialog to Mission projections |
| [0128](0128-registered-artifact-text-preview.md) | Preview registered Artifact text through Mission Control |
| [0127](0127-experimental-anonymous-browser-read.md) | Evaluate one anonymous browser read behind a dedicated Native profile |
| [0126](0126-frozen-reviewed-skill-catalog.md) | Freeze operator-reviewed document Skills before model use |
| [0125](0125-experimental-sandbox-native-composition.md) | Compose the Native Harness with an explicit sandbox capability |
| [0124](0124-strict-in-process-provider-budget-profile.md) | Bind an explicit provider profile to the Mission budget ledger |
| [0123](0123-persistent-mission-budget-ledger.md) | Persist an internal Mission budget ledger |
| [0122](0122-experimental-opensandbox-http-port.md) | Evaluate a pinned OpenSandbox behind an opt-in HTTP port |
| [0121](0121-trusted-local-test-bundles.md) | Admit signed test bundles under the execution lease |
| [0120](0120-development-service-failure-boundaries.md) | Seal privileged development services and fail closed on sandbox loss |
| [0119](0119-default-task-artifact-acceptance.md) | Share explicit output-integrity acceptance across entry points |
| [0118](0118-composable-agent-integrations.md) | Compose external capabilities behind Mission-owned execution |
| [0110](0110-checkpoint-resume-storage-compatibility.md) | Preserve checkpoint migration lineage and resume storage compatibility |
| [0108](0108-event-log-as-memory-multi-agent-collaboration.md) | Event log as memory for multi-agent collaboration |
| [0107](0107-memory-slimming-web-chat-decommission.md) | Slim the memory subsystem to L0/L1 |
| [0106](0106-l3-knowledge-graph-stays-r5.md) | L3 knowledge graph stays on the R5 timeline |
| [0105](0105-multimodal-vision-input-dual-track.md) | Multimodal vision input via dual-track content parts |
| [0104](0104-optional-newapi-llm-gateway.md) | Optional new-api LLM gateway (supplier layer) |
| [0103](0103-single-entry-desktop-orchestration.md) | Single-entry desktop orchestration |
| [0102](0102-desktop-sidecar-artifact-root.md) | Desktop sidecar artifact root |
| [0101](0101-desktop-authenticated-session-probe.md) | Desktop authenticated session probe |
| [0100](0100-desktop-control-plane-reachability.md) | Desktop control-plane reachability |
