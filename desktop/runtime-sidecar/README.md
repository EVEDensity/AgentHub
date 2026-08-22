# AgentHub Runtime Sidecar

This is the small local process owned by the desktop shell. It is deliberately
not the Python Mission Runner and does not claim or execute Mission, Contract,
WorkUnit, Artifact, Evidence, Decision, or Outcome state.

The current sidecar owns one boundary: a loopback readiness endpoint at
`http://127.0.0.1:18097/readyz`. It is bounded to `GET` requests and returns
the versioned runtime health contract. The Tauri shell supervises its process
and validates this response before showing the Runtime as ready.

Build it from this directory with Rust 1.88:

```powershell
.\build-windows.ps1
```

The script builds the locked dependency graph and stages the target-triple
filename expected by Tauri `externalBin`. Model execution and Mission work
remain explicit future sidecar capabilities, not synthetic health success.
