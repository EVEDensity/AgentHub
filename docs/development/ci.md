# CI ownership and verification

> Status: implemented  
> Owner: repository maintainers  
> Last reviewed: 2026-10-09  
> Scope: GitHub Actions validation, release checks and external evidence

## Deterministic merge gate

[`ci.yml`](../../.github/workflows/ci.yml) runs on pull requests targeting `main`,
pushes to `main`, and manual dispatch. Feature branches use the PR run rather
than a second branch-push run. A new update cancels the previous run for that PR.
Workflow permissions default to read-only repository access.

Always-required checks are:

- Quality: documentation links and claim discipline, Python runtime syntax/name
  errors, size/complexity regressions against the base revision, and measured
  offline compaction, mock streaming and native-tokenizer parity gates.
- Python core: the complete `tests/` and `app/` pytest suite on the declared
  minimum Python version, 3.11, plus compilation of production Python services.
  CLI end-to-end tests are enabled. Go is installed so the real Stateless MCP
  Gateway contract test runs rather than skipping for a missing toolchain.
- PostgreSQL: all `tests/integration/` tests with a disposable PostgreSQL 16
  service, including the complete migration graph, original-head upgrades,
  downgrade/reupgrade, rollback, concurrent locks and checkpoint recovery.

The core suite explicitly skips opt-in infrastructure cases without their
service configuration; the PostgreSQL job supplies the database configuration
and runs those cases against a real database. Skipped external-service cases
do not establish production readiness.

[`scripts/ci_changes.py`](../../scripts/ci_changes.py) selects additional jobs
from the changed dependency paths. Frontend/API changes run TypeScript,
Vitest, a production build and Playwright browser checks in one job. Go changes
run vet and race tests for every workspace module. Rust changes run locked
workspace tests. CLI/runtime/dependency changes run CLI, desktop and release
contract tests, including real CLI subprocesses, on Windows/Python 3.12.
Public contract changes select all runtime consumers. Container builds follow
the Go, Python service and frontend inputs; deployment/runtime service changes
also start and health-check the Docker Compose smoke stack. CI configuration
changes and manual runs select every optional job.

`CI Gate` requires all core checks and every selected optional check to pass.
A failed, cancelled, missing or unexpectedly skipped selected check fails the
gate. A missing selection output also fails. Unaffected optional jobs may skip.
Junit reports, quality reports and failing browser traces are uploaded for
diagnosis. No validation step uses `continue-on-error`.

Maintainers should configure branch protection to require **CI Gate**, replacing
the old individual contract/checkpoint/CLI workflow statuses. Removing a workflow
does not automatically remove its required status from repository settings.

## Consolidation and known gaps

The public-contract, checkpoint-migration and CLI JSONL workflows were merged
into the main workflow. Their actual tests remain covered; runtime and contract
dependencies are installed together. Frontend unit and browser jobs share one
dependency installation/build, and all real PostgreSQL tests share one service.

The old Python-services job collected no tests and its coverage check skipped.
It did not validate the root application suite. It was replaced by the complete
core suite and service compilation. Existing root tests also exercise service
adapters, but this does not imply a coverage percentage for all Python services.

Unconfigured frontend ESLint and baseline-free Python mypy/style and Rust
fmt/clippy steps previously ran with ignored failures. Those ineffective steps
were removed. Strict Python runtime-error checking, Go vet, TypeScript checking,
compilation and actual tests remain enforced. Frontend style, broad Python
typing/style and Rust formatting/clippy debt still need a separately reviewed
baseline and remediation; the green gate does not claim those checks pass.
The full historical size/complexity audit remains available locally; incremental
CI rejects growth rather than expanding static exemptions for old debt.
New Python modules may have at most 500 effective lines and new functions a
branch complexity score of 15. Existing modules/functions retain the normal
800-line/20-complexity limits or their measured base value when already over
that limit. Deletion removes debt. Copies and renames count as new files and
must meet new-module limits; changing a path cannot grant a legacy exemption.
Git filenames are read with NUL delimiters, including names containing spaces
or Unicode, and conditionally defined/nested functions are checked by scope.

The three third-party AI comment-agent workflows were removed: Cursor was a
Claude alias, the issue-comment checkout referenced a nonexistent event field,
and generated edits used unvalidated path/shell interpolation. AgentHub's own
optional verifier-gated `review-pr` workflow remains.

## Tokenizer provisioning

Before offline validation, [`ci_prepare_tokenizers.py`](../../scripts/ci_prepare_tokenizers.py)
downloads and verifies the SHA-256 checksums of `cl100k_base` and `o200k_base`.
The quality job also provisions Qwen from a pinned immutable model revision,
checks its checksum, and loads it through the production tokenizer path.
Cached assets are checked again; corruption or failed provisioning fails the
job. Tests do not unexpectedly download assets during execution, and the native
parity gate cannot turn a download failure into a successful skip.

## External evidence and model review

[`cli-provider-nightly.yml`](../../.github/workflows/cli-provider-nightly.yml)
consolidates real provider, benchmark, SSE recovery and vision evidence. Daily
scheduled jobs are disabled until the respective repository variable equals
`true`; manual dispatch selects the requested probes. Missing credentials or
configuration after explicit enablement fail the selected job.

- `AGENTHUB_PROVIDER_CHECKS_ENABLED` enables streaming, tool calls, real Mission
  execution and responsiveness benchmarks. It requires `DEEPSEEK_API_KEY`.
- `AGENTHUB_SSE_EVIDENCE_ENABLED` requires `AGENTHUB_SSE_BASE_URL`,
  `AGENTHUB_SSE_MISSION_ID`, secret `AGENTHUB_CLI_AUTH_TOKEN`, and an explicitly
  configured `AGENTHUB_SSE_FAULT_INJECTED` declaration. Configure the disconnect
  proxy first; the workflow does not assert that fault injection occurred merely
  by running on CI. The probe must observe reconnect and cursor recovery.
- `AGENTHUB_VISION_CHECKS_ENABLED` requires `NEWAPI_BASE_URL` and
  `AGENTHUB_TEST_CHANNEL_KEY` secrets for the real multimodal channel.

[`review-pr.yml`](../../.github/workflows/review-pr.yml) requires
`AGENTHUB_PR_REVIEW_ENABLED=true`, `AGENTHUB_REVIEW_MODEL_API_KEY`, and a same-repo
PR from an owner, member or collaborator. Forks cannot receive model credentials.
The default provider/model can be overridden by `AGENTHUB_REVIEW_PROVIDER` and
`AGENTHUB_REVIEW_MODEL`. Enabled review failures and blocking findings still fail
its status; deterministic CI does not depend on buying a model channel.

## Releases and local reproduction

`github-cli-release.yml` builds, freezes, verifies and publishes checksummed
Windows x64 CLI assets only on `cli-v*` tags. `cli-package-install.yml` manually
verifies two selected public versions through install, upgrade and rollback.
`desktop-windows.yml` remains a separate manual/tag-driven desktop packaging,
signing, GUI and updater/release stack verification workflow. These workflows
remain separate because their inputs and publishing permissions differ from PRs.

```powershell
python -m pip install -r requirements-dev.txt -r tests/contracts/requirements.txt
python scripts/ci_prepare_tokenizers.py --qwen
python -m ruff check --select E9,F63,F7,F82 app services/python scripts
python -m compileall -q app services/python scripts
$env:AGENTHUB_CLI_E2E = "1"
python -m pytest tests app -q --tb=short
python benchmarks/gates.py quality --base-ref origin/main --output .tmp/quality-report.json
```

Database checks additionally require `AGENTHUB_TEST_POSTGRES_DSN` pointing to a
disposable test database. See [integration test setup](../../tests/integration/README.md).
Validate workflow expressions with `actionlint`; parsing YAML alone cannot
detect unavailable GitHub expression contexts or invalid job dependencies.
