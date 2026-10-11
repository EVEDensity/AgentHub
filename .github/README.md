# GitHub automation

`workflows/ci.yml` owns deterministic pull-request validation and the stable
`CI Gate` status. Python contracts, CLI checks and PostgreSQL migration checks
belong there instead of separate overlapping workflows.

Release packaging is separate from PR validation: CLI tags, desktop tags and
the manually selected install/upgrade/rollback probe have distinct inputs.
Paid provider checks, external recovery evidence and model-assisted PR review
are explicitly enabled integrations, not prerequisites for an ordinary PR.

See [the CI guide](../docs/development/ci.md) for coverage, opt-in configuration,
local commands and the required-check migration.
