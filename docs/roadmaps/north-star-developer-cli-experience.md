# AgentHub CLI North Star and Delivery Plan

> Status: accepted baseline
> Owner: CLI and architecture maintainers
> Last reviewed: 2026-09-03
> Scope: developer-facing CLI, streaming, repository workflow, CI, and release

## 1. Product goal

After installing `agenthub`, a developer can run:

```bash
agenthub "修复这个 bug，并运行测试证明"
```

inside a Git repository and receive a continuous, auditable workflow:

```text
plan -> edit -> sandbox/tool execution -> verification -> reviewable diff
```

The CLI is a projection and command surface. Mission Control remains the only
source of durable Mission, WorkUnit, Artifact, Evidence, Decision, and Outcome
truth. The CLI never runs a parallel model loop or bypasses verification.

## 2. Current baseline (2026-09-03)

### Implemented

- Mission/WorkUnit/Artifact/Evidence/Decision lifecycle and independent verifier.
- Local SQLite execution through Runner and Harness with bounded budgets.
- `agenthub init`, `run`, `exec --json`, `chat`, `tui`, `missions`, `search`,
  `replay`, `facts`, and `review-pr`.
- Layered `AGENTS.md`, skills loading, project facts, resume, compact context,
  Git diff rendering, Rich panels, Spinner, and session cost summaries.
- Mission and Session HTTP SSE endpoints with authenticated frontend consumers.
- CLI Mission SSE consumer with cursor-based reconnect and polling fallback.
- CLI hooks for `assistant.delta` events and immediate Decision handling.

### Known gaps

- The CLI event stream is not yet the complete producer path for model text.
  Harness/Model Adapter must publish real `assistant.delta` events during model
  generation; durable checkpoints remain content-minimized.
- Tool output and file-change events need a stable public streaming contract and
  richer rendering (`tool.started`, `tool.output`, `tool.completed`, diff/undo).
- REPL, one-shot prompt, and Textual TUI need one shared session/event reducer.
- JSONL event output, public npm release, signed desktop release, shell
  completion, and public capability benchmarks are release work.

### Delivered through Phase 3 (2026-09-03)

- Interactive CLI now exposes `/diff`, `/changes`, and `/patch` for reviewable
  repository state, plus confirmed `/undo` for tracked worktree changes.
- `/undo` never removes untracked files and no command performs an implicit
  commit; commit remains an explicit user workflow.

### Delivered through Phase 4 (2026-09-03)

- Long sessions expose `/context` state, emit 70/85/95% token-budget notices,
  and keep `/compact` output bounded to 12,000 characters before reinjection.

## 3. Competitive gap

Compared with Claude Code, Codex CLI, Aider, Gemini CLI, OpenCode, and Goose,
AgentHub's durable state, independent verification, evidence chain, and A2A
boundaries are stronger. Mature tools are ahead in first-run installation,
repository-centric interaction, continuous token/tool output, diff/undo flow,
permission ergonomics, and published distribution.

The priority is productizing the existing engine, not creating another
execution runtime.

| Dimension | AgentHub baseline | Target behavior |
|---|---|---|
| Entry | Python module plus prepared packaging | `agenthub` from a clean machine |
| Interaction | `chat`, `run`, and `tui` overlap | one shared session and reducer |
| Streaming | SSE consumer and status Spinner | text, tool, state, and verification events |
| Permissions | mission/decision hooks and tiers | clear tool-level allow/deny/always UX |
| Code workflow | changed-files and diff panel | `/diff`, `/changes`, `/undo`, patch preview |
| Verification | independent verifier and Evidence | visible PASS/FAIL proof in every completion |
| Context | AGENTS.md, facts, compact, resume | automatic budget signals and context state |
| Automation | `exec --json`, review-pr | versioned JSONL stream and CI contract |
| Distribution | build pipelines and manifests | public npm and signed desktop releases |

## 4. Streaming contract (v1)

All human, JSON, and JSONL renderers consume the same event shape:

```json
{
  "schemaVersion": 1,
  "eventId": "evt-...",
  "sequence": 42,
  "missionId": "mis-...",
  "workUnitId": "wu-...",
  "attempt": 1,
  "type": "assistant.delta",
  "occurredAt": "2026-09-03T00:00:00Z",
  "payload": {"text": "正在检查登录逻辑..."}
}
```

Minimum event vocabulary:

```text
mission.created / mission.started / mission.completed / mission.failed
work_unit.claimed / work_unit.running
assistant.delta / assistant.completed
tool.started / tool.output / tool.completed
checkpoint.created
decision.pending / decision.resolved
artifact.registered
verification.started / verification.completed
```

`assistant.delta` is a streaming event, not a durable checkpoint payload.
Events are ordered and deduplicated by `eventId`; `afterSequence` (or an
equivalent cursor) is required for reconnect. A disconnected SSE stream falls
back to short polling and resumes the stream when available.

## 5. Target architecture

```text
Mission API -> StreamConsumer (SSE / JSONL / polling)
                    |
              EventReducer
                    |
       +------------+-------------+
       |                          |
 Human Renderer (Rich/Textual)  JSON/JSONL Renderer
```

`MissionClient` owns HTTP commands only. `StreamConsumer` owns transport and
cursor/reconnect. `EventReducer` owns transient CLI view state. Renderers own
presentation and never mutate Mission state. Decision commands go back through
Mission Control. Harness publishes model/tool callbacks through Runner; it
does not call the CLI.

## 6. Delivery phases and acceptance

### Phase 0: protocol and baseline

- Version the CLI event schema and cursor semantics.
- Unify `chat`, bare prompt, and TUI around one session facade.
- Add ordering, duplicate, reconnect, and old-server fallback tests.

Acceptance: SSE disconnect/reconnect does not duplicate events; JSONL is
machine-parseable; old deployments remain usable through polling.

### Phase 1: real model streaming

- Add an async stream callback to the Model Adapter/Harness boundary.
- Publish real `assistant.delta` and `assistant.completed` events.
- Keep checkpoint payloads content-minimized.
- Render deltas immediately in Rich and Textual clients.

Acceptance: text appears before model completion; the final Mission result and
Verifier remain authoritative; reconnect does not duplicate text.

### Phase 2: tools and human-in-the-loop

- Publish `tool.started`, bounded `tool.output`, and `tool.completed`.
- Publish `decision.pending` and resolve it immediately in the stream loop.
- Expose clear `suggest`, `edit`, `auto`, and dangerous-command confirmation.

Acceptance: denied tools never execute; allowed tools resume the same attempt;
Decision failures fail closed.

### Phase 3: repository workflow

- Add `/diff`, `/changes`, `/undo`, `/patch`, and optional `/commit`.
- Show file-level summaries and test output without flooding the terminal.
- Protect paths outside the selected repository and preserve failed diffs.

Acceptance: every modification is reviewable, reversible, and tied to the
Mission attempt; the CLI never auto-commits without explicit user command.

### Phase 4: context and long sessions

- Add `/context` and automatic 70/85/95% budget notices.
- Preserve objectives, acceptance criteria, recent edits, and verification when
  compacting or resuming.
- Keep receipts/replay/facts backed by Mission event truth.

Acceptance: a ten-turn session can resume without invented history or lost
acceptance criteria.

### Phase 5: CI and integrations

- Version `--json` and add `--jsonl` live events.
- Keep stdout clean in machine modes and preserve stable exit codes.
- Harden `review-pr` and GitHub Action around Evidence and Artifact summaries.

Acceptance: CI can consume events incrementally and returns non-zero for failed
verification, denied work, timeout, or infrastructure failure.

### Phase 6: release and ecosystem

- Publish the first npm CLI package and signed desktop release.
- Add shell completion, upgrade/rollback, diagnostics, and public benchmarks.
- Validate a clean machine with no Python, Node, Docker, or repository checkout.

Acceptance: a new developer can install, enter a repository, run a real task,
review the diff, and inspect verification evidence without Web UI setup.

## 7. Non-goals and guardrails

- No second model loop in the CLI.
- No bypass of Mission Control, lease fencing, Evidence, or independent
  verification.
- No model正文 in durable checkpoints and no synthetic success events.
- No default vector-memory dependency or heavy external memory service.
- No automatic commit or unrestricted tool execution.
- Legacy WebSocket and registry paths may remain for compatibility, but new CLI
  behavior must use the versioned Mission/SSE contract.

## 8. Work protocol

Each Phase is a large task. Stop after completing a Phase and report evidence.
Each independently verifiable small task is committed separately. Do not push
from the implementation workflow. Update this document after every Phase with
links to implementation and tests; claims without executable evidence remain
targets rather than shipped capabilities.
