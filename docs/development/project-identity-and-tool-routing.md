---
state: implemented
---

# Project Identity And Tool Routing

## Scope

The CLI and every model-backed execution path use `ProjectManifest` as the
deterministic source for the current project identity. The manifest is derived
from the bound workspace, not from model inference.

## ProjectManifest

`app/services/project_manifest.py` discovers, without modifying the workspace:

- project name from `package.json`, `pyproject.toml`, or the directory name;
- detected technology manifests;
- a bounded README summary;
- current Git branch and a credential-redacted `origin` URL;
- root `AGENTS.md` or `CLAUDE.md` instruction files.

`to_prompt()` is injected into direct chat, Mission/Runner instructions, and
the shared workspace prompt. Full files and diffs remain available only through
read-only tools.

## Tool routing

`project_inspect` is a read-only, workspace-bounded tool that returns the same
manifest fields as JSON. Project identity questions are answered locally by the
CLI and do not create an Attempt snapshot or Mission.

Git read requests use the existing Git tools. `git_commit` remains an explicit
side-effect operation. `git_push` is registered so capability discovery is
honest, but the Desktop Runner deliberately returns
`unsupported_capability` and requires the user to push manually.

## Verification

The contract is covered by:

- `tests/services/test_project_manifest.py`;
- `tests/services/test_project_tools.py`;
- `tests/cli/test_cli_chat.py`;
- Desktop Runner whitelist assertions in
  `tests/services/test_desktop_local_runner.py`.

The local smoke command is:

```text
@("what is this project", "/quit") | python -m app.cli chat --provider mock
```

It must print the discovered project, workspace, Git metadata, technology
stack, and README summary without starting a Mission.
