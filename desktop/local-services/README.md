# Desktop local service staging

These Windows build scripts produce the internal local-services directory used
by the desktop supervisor. They do not start services, verify native WebView2,
sign an installer, or establish a Mission outcome.

Run build-windows.ps1 from a clean committed source checkout. It always freezes
Mission Control from this checkout, builds the standalone Next.js frontend and
the two Go gateways, and writes the supervisor-compatible stack-manifest.json.
That manifest retains schemaVersion 1, services and version, and records the
full source HEAD. Git failures, a dirty checkout, a changed HEAD or failed
compiler fail the build; an older executable is never reused as success.

Both scripts accept OutputDirectory and PythonExecutable. The default stage is
desktop/src-tauri/local-services; the default interpreter is the active python
command. An explicit PythonExecutable selects another installed environment.
Install the project runtime dependencies and PyInstaller in that environment;
the scripts use its own site-packages and do not assume a repository .venv.
The previous MissionControlBinary and BuildMissionControl inputs are removed.

The full build REPLACES its dedicated OutputDirectory. Filesystem roots, the
repository and its ancestors, source directories inside the repository, and
paths or trees containing reparse points are rejected before cleanup. Inside
the checkout, staging is limited to desktop/src-tauri/local-services and its
children, or a dedicated child of .tmp. Explicit external output directories
must also be dedicated build stages: their contents are replaced. The separate
build-mission-control.ps1 only replaces its own executable in that stage.

Next.js always builds into frontend/.next after the previous build directory
is checked and removed. It bakes Mission Control at 127.0.0.1:28000 and Gateway
at 127.0.0.1:28001 into the standalone rewrites. The fresh standalone, static
contents are copied with hidden files. Public resources are limited to the
checkout's git-tracked frontend/public files and the freshly prepared pinned
public/monaco/0.55.1 and public/xterm/6.0.0 directories. Those two generated
directories are checked and removed before build preparation, and any public
directory traced into standalone is replaced by this allowlist. Other ignored
public files, older asset versions and arbitrary charts are left on disk but
excluded from staging. Only the unused Next development overlay
is pruned for Windows archive path length; runtime assets and source maps are
not removed by broad filename patterns. This build still has the documented
first-instance port-group limitation.

The full build restores inherited API_BACKEND, API_BACKEND_URL,
MISSION_CONTROL_URL, GO_GATEWAY_URL, GOCACHE, NEXT_DIST_DIR and PYTHONPATH in a
finally block. Mission Control freezing also independently restores PYTHONPATH.
Its temporary PyInstaller work/spec directory is unique under .tmp for each
invocation and remains available for diagnosis. Neither script rewrites or
restores user source files.

The supervisor stack manifest is distinct from the package resource inventory.
The packaging layer must inventory and verify the completed stage before
claiming packaged UI resources; browser-preview checks and native WebView2
acceptance remain separate evidence scopes.
