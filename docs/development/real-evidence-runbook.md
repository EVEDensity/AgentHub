# Real Evidence Runbook

> Status: target  
> Owner: CLI maintainers  
> Last reviewed: 2026-09-06  
> Scope: external provider, TTY, PostgreSQL, and npm acceptance

Run `python scripts/verify_real_evidence.py` first. `SKIP` is an honest result
when a secret, TTY, database, or package manager is unavailable.

The CI workflows `.github/workflows/cli-provider-nightly.yml`,
`npm-cli.yml`, and `cli-package-install.yml` are the authoritative places for
real provider and registry evidence. Attach their redacted artifacts and run
URLs before upgrading a capability to `production-verified`.

Required evidence includes DeepSeek v4-flash/v4-pro text streaming and native
tool-call, physical TTY widths 40/80/120, an injected SSE disconnect followed
by `Last-Event-ID` recovery, and clean Windows/macOS/Linux npm install,
upgrade, and rollback. PostgreSQL evidence must use two application processes
and prove that durable ledger replay still works when NOTIFY is delayed.
