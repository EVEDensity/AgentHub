# Code Quality Standard and Review Process

> Status: accepted
> Owner: repository maintainers
> Last reviewed: 2026-09-02
> Scope: `app/`, `frontend/`, `services/`, `desktop/`, `tests/`
> Applies to: every pull request, including AI-generated patches

## 1. Purpose

AgentHub sells trust: verifiable execution, honest Evidence, fail-closed
security. Code quality rules exist to keep the core loop small, replayable,
observable, and cheap, and to make the repository safe to modify for humans and
AI agents alike.

## 2. Structural gates (CI-enforced)

| Gate                        | Threshold                                                                                                                                                                                                                      | Enforcement                                    |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------- |
| Maximum module size         | **500 LOC hard limit for all new modules**; legacy modules carry an 800 LOC soft ceiling — anything over that must be registered in §2a below with a shrink target. Every refactor slice must move the number *down*, never up | CI size check + review against §2a             |
| Maximum function complexity | cyclomatic complexity < 15 per function                                                                                                                                                                                        | CI lint (ruff for Python, biome/eslint for TS) |
| Maximum function length     | < 100 lines                                                                                                                                                                                                                    | CI lint                                        |
| Broad exception use         | No new bare `except Exception` in hot paths; require `except SpecificError`                                                                                                                                                    | Review + CI lint rule                          |
| File layout                 | New business logic goes under `app/services/<domain>/`; new WS lanes under `app/api/websocket/`; new benchmarks under `benchmarks/`                                                                                            | Review                                         |
| Test placement              | Unit/near-module tests may live beside modules; structural/contract tests live under `tests/`                                                                                                                                  | Review                                         |

### §2a — Legacy exception registry (baseline 2026-09-02)

Modules over the 800 LOC soft ceiling are registered here with a shrink target
and a owning roadmap phase. A refactor that does not move the number *down*
does not count toward the phase stop condition; if it moves the number *up*,
it is rejected unless there is no alternative and the architect signs off.

The registry is ratcheted on each phase review. A module that falls below 800
LOC is removed from the registry; a module that climbs *above* 800 LOC
between two reviews is treated as a *new* debt item with a target date
of the next phase boundary.

| Baseline LOC | File                                         | Target LOC    | Roadmap phase | Notes                                                                                |
| ------------ | -------------------------------------------- | ------------- | ------------- | ------------------------------------------------------------------------------------ |
| 1791         | `app/services/tools/builtin_tools.py`        | \~800         | R3            | Move each tool group into its own file under `services/tools/builtins/`              |
| 1542         | `app/services/runner_service.py`             | \~600         | R3            | Claim/lease/mission-dispatch mix — split into `claim/`, `lease/`, `lifecycle/` lanes |
| 1401         | `app/repositories/mission_repository.py`     | \~700         | R3            | CRUD / search / lineage mix — split into methods by use case                         |
| 1244         | `app/api/files.py`                           | \~600         | R3            | Upload / download / metadata / indexing lanes                                        |
| 1049         | `app/db/migrations/mission_control_plane.py` | — (immutable) | —             | Migration file — exempt; only grows forward, never refactored                        |
| 1018         | `app/services/adapter_manager.py`            | \~500         | R3            | Discovery / lifecycle / protocol-dispatch lanes                                      |
| 991          | `app/services/desktop_runner_tools.py`       | \~500         | R3            | Split by tool family (sandbox, git, process)                                         |
| 960          | `app/services/tools/definitions.py`          | \~500         | R3            | Inline definitions → per-tool-class files                                            |
| 892          | `app/services/agent/tooling.py`              | \~500         | R3            | Tool discovery + runtime binding + formatter lanes                                   |

**Recently resolved (removed from registry):**

| File                         | Peak LOC | Resolution                                                                                                        | Date       |
| ---------------------------- | -------- | ----------------------------------------------------------------------------------------------------------------- | ---------- |
| `app/api/v1/chat_mission.py` | 915      | Split into `chat_mission/_helpers.py` (323) + `chat_mission/_handlers.py` (619) + `chat_mission/__init__.py` (36) | 2026-09-02 |
| `app/api/chat.py`            | 816      | Split into `chat/_helpers.py` (236) + `chat/_routes.py` (476) + `chat/__init__.py` (21)                           | 2026-09-02 |

Modules in the 600–800 LOC band (`app/db/init_db.py` 778, `app/cli/main.py` 743,
`app/cli/runtime.py` 732, `app/services/a2a_adapter_service.py` 709, ...) are
*not* on the registry — they are encouraged to shrink opportunistically but
do not block any phase. If any one of them crosses 800 LOC it is added.

## 3. Coverage expectations

- New services and state transitions: unit tests under `tests/services/` plus
  contract tests under `tests/contracts/` when a versioned API is touched.

- New domain state: domain + transitions tests under `tests/domain/`.

- New transport: integration tests under `tests/integration/`.

- Frontend commands: primary-path e2e coverage in `frontend/e2e/`.

- Performance-sensitive changes: contributor must run the affected benchmark in
  `benchmarks/` and report before/after numbers in the PR description.

## 4. Documentation-to-code rule (no unverified claims)

- Every public claim in `docs/` and `frontend/` must link to a test or an
  implementation location, or be worded as **target** or **prototype**.

- A performance figure (e.g. "P95 < 80ms") may appear only if a matching gate in
  `benchmarks/` proves it on CI, otherwise it must be marked as a target value.

- Changing an implementation must not silently invalidate a doc; update the doc
  in the same PR or mark it superseded per the documentation standard.

## 5. Review checklist (applies to all PRs)

1. Does the change introduce new business state outside Mission/WorkUnit?
   If yes, reject; route through Mission Control first.
2. Are state transitions transactional and covered by tests?
3. Is every `except Exception` justified and specific?
4. Are secrets and credentials in the code? (Never.) Is the credential store
   used the OS/native one, not env/config files?
5. Does the module exceed the size/complexity gates? If it does, is it already
   registered in the reconstruction roadmap as debt, and is it shrinking?
6. Do docs referenced by the change still match the implementation?
7. Is there evidence (test run, benchmark report) backing any new claim?

## 6. AI-agent review contract

AI agents must start at `AGENTS.md`, then `docs/README.md`, then the nearest
module README and its tests. They must not invent behavior from stale docs;
when documentation and code disagree, code and tests win, and the discrepancy
is reported back as a doc-fix task. Generated patches must satisfy the same
gates as human patches, including the checklist in §5.

## 7. Flow when a gate fails

1. CI blocks merge.
2. The PR author fixes or explicitly demotes the claim.
3. Oversize refactors are broken into reviewable slices; each slice keeps the
   module shrinking relative to the previous slice.
4. Only architecture maintainers may register new debt items in the
   reconstruction roadmap; unregistered debt is fixed before merge.

