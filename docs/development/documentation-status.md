# Documentation Status and Evidence

> Status: implemented  
> Owner: repository maintainers  
> Last reviewed: 2026-09-06  
> Scope: capability status vocabulary

`implemented` means code exists and automated tests pass. `production-verified`
requires that proof plus evidence from the applicable real provider, physical
terminal, released platform, or production deployment. `target` is incomplete
or planned. `superseded` must link to the replacement document.

Evidence levels are orthogonal: `unit`, `contract`, `integration`,
`real-provider`, `real-tty`, `cross-platform`, and `production`.

Do not use `partial`, `pending`, or `complete` as capability statuses. Replace
them with `target` or `implemented`, and record proof links. Fixture tests never
qualify as `production-verified`.

```text
Status: implemented
Evidence: contract, integration
Proof: tests/...; app/...
Production evidence: none (or CI run/artifact URL and date)
```

Superseded documents must contain an explicit `Replacement:` link and must not
continue to describe active behavior.
