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
| [0105](0105-multimodal-vision-input-dual-track.md) | Multimodal vision input via dual-track content parts |
| [0104](0104-optional-newapi-llm-gateway.md) | Optional new-api LLM gateway (supplier layer) |
| [0103](0103-single-entry-desktop-orchestration.md) | Single-entry desktop orchestration |
| [0102](0102-desktop-sidecar-artifact-root.md) | Desktop sidecar artifact root |
| [0101](0101-desktop-authenticated-session-probe.md) | Desktop authenticated session probe |
| [0100](0100-desktop-control-plane-reachability.md) | Desktop control-plane reachability |
