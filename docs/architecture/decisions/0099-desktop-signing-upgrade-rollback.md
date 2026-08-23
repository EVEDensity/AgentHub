# ADR-0099: Desktop Signing, Upgrade, and Rollback Policy

> Status: policy defined  
> Owner: desktop maintainers  
> Date: 2026-08-23

## Decision

Unsigned MSI/NSIS and portable ZIP artifacts are internal validation outputs.
The public release workflow must fail closed unless the certificate payload,
certificate password, and updater private key are injected from CI secrets or a
managed Windows certificate store. Secrets are never committed or written into
release manifests.

Every release uses the Tauri product version as the single version source and
publishes the existing SHA-256 manifest alongside installers. The updater
manifest is generated from the same version and target metadata and is signed
with the injected updater key. A client downloads an update to a staged path,
verifies its signature and digest, keeps the previous installation until the
new executable passes startup/readiness checks, and restores the previous
version when verification or first launch fails. The previous version is
retained for one rollback window and then removed by a later maintenance task.

The portable ZIP remains an internal troubleshooting channel and is never used
as an in-place updater.

## Verification

`desktop/release-policy.ps1 -PublicRelease` is a release gate. It only checks
that the required secret channels exist; signing and updater integration must be
performed by the Windows release runner and recorded as CI artifacts. No local
developer build may claim public release readiness without that job evidence.
