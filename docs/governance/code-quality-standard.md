# Code Quality Standard and Review Process

> Status: accepted
> Owner: repository maintainers
> Last reviewed: 2026-10-10
> Scope: `app/`, `frontend/`, `services/`, `desktop/`, `tests/`
> Applies to: every pull request, including AI-generated patches

## 1. Purpose

AgentHub sells trust: verifiable execution, honest Evidence, fail-closed
security. Code quality rules exist to keep the core loop small, replayable,
observable, and cheap, and to make the repository safe to modify for humans and
AI agents alike.

## 2. Structural gates and review rules

The [CI guide](../development/ci.md) describes the actual required checks.
`benchmarks/gates.py quality --base-ref <SHA>` compares Python changes with the
Git merge base. The workflow supplies the base SHA from GitHub's event metadata;
the checker does not consult a developer's `BASE_REF` environment override.
An invalid base or invalid Python syntax fails the check.

The incremental gate covers `.py` files under `app/`, `services/`, `benchmarks/`,
`tests/` and `scripts/`. Effective lines are nonblank source lines excluding lines
whose first nonspace character is `#`. Do not compress statements onto fewer
lines to bypass the measure; review still checks readability.

- New modules: at most **500 effective lines**. Copies and renames count as new
  paths and cannot inherit another module's allowance.
- Existing modules: at most **800 effective lines**, or their measured merge-base
  value when already above 800. An oversized legacy module may stay the same or
  shrink; it cannot grow. Refactoring acceptance requires an actual reduction.
- New functions, methods and lambdas: branch complexity at most **15**. Existing
  functions retain the normal **20** limit, or their measured merge-base value
  when already above 20. Nested and conditional definitions are checked in their
  own scopes; their bodies do not inflate the enclosing function's score.
- Lambda identities use their enclosing scope, direct assignment binding when
  available, and occurrence number. Moving comments or reformatting does not
  erase an existing lambda's baseline. A new binding cannot inherit a different
  binding's legacy complexity allowance.
- Deletions remove debt. NUL-delimited Git paths preserve Unicode and spaces.

Python runtime syntax/name errors (`E9,F63,F7,F82`), compilation and the complete
root test suite are required. TypeScript checks, unit/browser tests and production
builds run for selected frontend/API changes; Go vet/race and locked Rust tests
run for selected consumers. These checks do **not** enforce a universal style,
function-length or coverage-percentage threshold. Frontend lint, broad Python
style/typing and Rust fmt/clippy need separate baselines before becoming gates.

Review rules supplement executable checks:

- Prefer functions below 100 lines and cohesive modules; this is a review target,
  not a currently enforced CI length check.
- Use specific exceptions in hot paths. Broad catches require a documented
  boundary purpose, such as final CLI error projection or supervised cleanup.
- Place new business logic under `app/services/<domain>/`, transport lanes under
  their adapter, and benchmarks under `benchmarks/`.
- Preserve compatibility imports during mechanical splits; update the nearest
  README whenever a module's responsibility changes.
- Historical migration revision IDs and SQL behavior are immutable. SQL constants
  may move mechanically into small modules with original aliases and value/order
  equivalence tests; do not rewrite already applied migrations.

### 2a. Historical audit and exemptions

The full `code_file_size` and `code_complexity` audit remains available through
`benchmarks/gates.py run --name <gate>`. Its historical metric is distinct from
incremental enforcement: file size counts all nonblank lines, and complexity
retains the older raw AST walk. The full complexity audit also lists lambdas.
Compare before/after snapshots with the **same scanner**; a changed scanner must
not be presented as a source-code debt reduction.

[`benchmarks/quality_exemptions.json`](../../benchmarks/quality_exemptions.json)
contains the remaining full-audit legacy allowances. They do not control the
incremental PR gate. Remove retired entries when appropriate; never expand or add
static allowances merely to make an audit pass. The live PR baseline is the
actual merge-base source, rather than an old documentation table.

This execution slice split the Harness, Runner and database implementation while
preserving contracts; see [execution follow-up slices](../roadmaps/execution-next-slices.md).
That acceptance requires a smaller comparable audit and preserved state/lease
checks. It does not require unrelated historical modules to reach zero debt.

## 3. Coverage expectations

- New services and state transitions: unit tests under `tests/services/` plus
  contract tests under `tests/contracts/` when a versioned API is touched.

- New domain state: domain + transitions tests under `tests/domain/`.

- New transport: integration tests under `tests/integration/`.

- Frontend commands: primary-path e2e coverage in `frontend/e2e/`.

- Performance-sensitive changes: contributor must run the affected benchmark in
  `benchmarks/` and report before/after numbers in the PR description.

These are evidence requirements, not a claim that CI enforces a numerical
coverage percentage. Add tests for meaningful behavior and failure boundaries;
implementation-mirroring tests do not replace state-transition evidence.

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
5. Does the change satisfy the actual merge-base size/complexity limits, and
   does a refactor reduce its measured debt without expanding static allowances?
6. Do docs referenced by the change still match the implementation?
7. Is there evidence (test run, benchmark report) backing any new claim?

## 6. AI-agent review contract

AI agents must start at `AGENTS.md`, then `docs/README.md`, then the nearest
module README and its tests. They must not invent behavior from stale docs;
when documentation and code disagree, code and tests win, and the discrepancy
is reported back as a doc-fix task. Generated patches must satisfy the same
gates as human patches, including the checklist in §5.

## 7. Flow when a gate fails

1. A required or selected check failure makes `CI Gate` fail. Branch protection
   must require that status to block merging; workflow success alone does not
   establish repository protection settings.
2. The PR author fixes or explicitly demotes the claim.
3. Oversize refactors are broken into reviewable slices; each slice keeps the
   module shrinking relative to the previous slice.
4. New or growing debt that violates the incremental limits is fixed before
   merge. A roadmap entry or static exemption does not bypass the gate.

