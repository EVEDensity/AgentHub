"""AgentHub developer CLI (North Star M0).

``python -m app.cli`` exposes the developer-facing surface documented in
``docs/roadmaps/north-star-developer-cli-experience.md``:

- ``init``: prepare a local ``.agenthub`` state directory;
- ``run``: drive one objective through the desktop local runner
  (bounded harness loop + sandboxed tools + VERIFY gate);
- ``exec``: headless variant of ``run`` with ``--json`` output and an
  exit code derived from the Mission terminal status.

The CLI never owns Mission state: it boots an isolated SQLite-backed
Mission Control subprocess and talks to it over the same versioned HTTP
API the desktop product uses.
"""
