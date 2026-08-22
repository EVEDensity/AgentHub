# AgentHub Desktop

> Status: minimum desktop shell
> Owner: desktop maintainers
> Last reviewed: 2026-08-22

`desktop/` is the native, user-facing entry point for AgentHub. It owns local
Runtime lifecycle, redacted diagnostics, OS integration, and future credential
storage. It does not own Mission, Contract, WorkUnit, Artifact, Evidence,
Decision, or Outcome state.

## First vertical slice

The Tauri shell serves a small static control view. It can read local Runtime
status and request start or stop through typed native commands. A start request
fails explicitly until secure Runtime onboarding is implemented; it never starts
Docker, a mock provider, or an unconfigured Runner.

## Layout

- `src-tauri/`: Tauri application, local process boundary, and native tests.
- `ui/`: dependency-free shell UI served by the desktop application.

## Development

The desktop shell uses Rust 1.88, fixed in `rust-toolchain.toml`; it is
intentionally independent from the server Rust workspace. Install the Tauri
CLI outside the product runtime, then run from this directory:

```powershell
cargo install tauri-cli --version "^2"
cargo tauri dev --manifest-path src-tauri/Cargo.toml
```

The initial shell intentionally has no Docker dependency. It is a lifecycle
surface, not a replacement control plane.

## Runtime sidecar contract

The native layer exposes a versioned `RuntimeSnapshot` contract. `status`
describes process lifecycle, while `readiness` describes whether the sidecar
has passed its health probe. These are intentionally separate: an alive process
is not evidence that the Runtime is ready to execute work. A terminated child
reports its exit code and an unhealthy readiness state. The contract contains
no Mission or WorkUnit data and is safe to evolve independently from the
control-plane API.
