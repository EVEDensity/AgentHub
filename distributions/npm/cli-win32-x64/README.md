# @agenthub/cli-win32-x64

Internal platform package: the frozen `agenthub.exe` Windows x64 binary
for `@agenthub/cli`. Do not install directly — install
[@agenthub/cli](https://www.npmjs.com/package/@agenthub/cli), which
pulls this package via optionalDependencies and execs the binary.

The binary is built from
[AgentHub](https://github.com/EVEDensity/AgentHub) by
`scripts/build-cli-windows.ps1` (PyInstaller onefile) and published by
the `npm-cli` release workflow on `cli-v*` tags.

License: Apache-2.0.
