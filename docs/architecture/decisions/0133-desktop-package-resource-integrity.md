# ADR-0133: Verify the resources extracted from one full desktop build

> Status: accepted\
> Owner: Desktop, frontend and delivery maintainers\
> Last reviewed: 2026-10-18\
> Scope: UI-4b full-service Portable and MSI resource integrity

## Context

The shell-only package did not include the shared Next.js interface. The
tag-only downloadable stack was a separate build. Existence-based reuse of an
ignored Mission Control executable and inherited `NEXT_DIST_DIR` could mix
older binaries or frontend output with a newly stamped commit. Hashing an
installer without extracting it did not establish its resource contents.

## Decision

Use a clean, committed checkout for `package-windows.ps1 -LocalServices`.
Rebuild Mission Control with the selected Python interpreter, both Go gateways
and the Next standalone frontend. Clear only validated build output locations;
never admit a caller-provided service executable as current build evidence.
Fix the frontend output to `.next` and bake Mission Control at
`127.0.0.1:28000`, gateway tools/platform at `127.0.0.1:28001`. Restore inherited
build environment variables even when a subprocess fails.

Create a complete file inventory after staging and pruning: relative path,
byte count and SHA-256, full source commit and full-service mode. Keep a copy
outside the package and embed the identical manifest as
`local-services/package-resources.json`. Preserve the existing runtime
`stack-manifest.json` as a separate compatibility projection. This inventory
is build metadata, not Mission Evidence, an execution grant or a signed
attestation of the build host.

One full-service installer invocation also creates the Portable ZIP from the
same staged bytes. Use .NET ZIP creation so hidden files are preserved; see
[Microsoft's archive behavior](https://learn.microsoft.com/powershell/module/microsoft.powershell.archive/compress-archive).
Use Tauri's preserved directory resources, documented in
[additional resources](https://v2.tauri.app/develop/resources/). Clean previous
bundle output before the build so old installers are never listed as current.

Independently extract the actual Portable ZIP and administratively extract the
actual MSI. Administrative extraction does not install or launch the product.
Compare the package executables with the just-built binaries and every local
service file with the external inventory. Reject missing, extra, modified,
wrong-commit, unsafe-path, aliased or linked resources. Record the outer
artifact's digest before and after inspection. Preserve failed reports and
mark success only after owned extraction-directory cleanup succeeds.

Mandatory resource semantics include the standalone entry point, build metadata,
all eight ordered rewrites, Next static scripts/styles, referenced local fonts,
Monaco workers and notices, xterm runtime/styles and actual component license
texts. Preserve the existing honest declaration-only notice for
`use-composed-ref`; it is not the missing upstream license text. This check
covers the selected frontend component notices, not a complete legal audit of
Python, Go, Node or all transitive runtime distribution licenses.

Deterministic PR/main CI builds both actual package forms on Windows from the
selected exact source SHA. Its stable merge gate requires the selected package
job and independently requires exactly one successful report for each form.
Fixture rejection tests cannot substitute for these two extraction reports.
The separate Windows distribution workflow also requests the full-service
profile. Tag-only stack publishing remains a separate downloadable artifact;
its build is not used as evidence for bytes inside the desktop package.

Reports are explicitly resource integrity only: native WebView2 and live
services are false. The unsigned PR/main package build does not establish
signatures, product login, Mission execution, clean-machine installation or
upgrades. Existing signing/lifecycle checks retain their own narrower scope.
UI-4c must identify the actual executable's WebView2 and use real login before
shared UI interaction can be accepted. UI-5 remains a clean-machine trial.

## Rollback

Revert the package inventory and its CI consumer together. This introduces no
business state, data migration, native command or frontend execution capability.
Preserve ADR-0132's browser/native/package distinctions and do not reinstate
existence-based reuse as same-source build evidence.
