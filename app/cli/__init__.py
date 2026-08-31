"""AgentHub developer CLI (North Star M0/M1).

``python -m app.cli`` exposes the developer-facing surface documented in
``docs/roadmaps/north-star-developer-cli-experience.md``:

- ``init``: prepare a local ``.agenthub`` state directory;
- ``run``: drive one objective through the desktop local runner
  (bounded harness loop + sandboxed tools + VERIFY gate), with layered
  ``AGENTS.md`` project instructions merged into the system prompt and
  optional ``--resume`` prior-mission context;
- ``exec``: headless variant of ``run`` with ``--json`` output and an
  exit code derived from the Mission terminal status;
- ``missions``: list missions recorded in the persistent local state;
- ``chat``: interactive REPL (north-star M2 baseline) — slash commands,
  per-turn status stream, and automatic resume chaining so consecutive
  turns accumulate prior objectives/statuses/summaries.
- ``tui``: full-screen Textual application (north-star M2) — streaming
  transcript, header status, input line; missions run in a thread
  worker with results marshalled back via call_from_thread.

The CLI never owns Mission state: it boots an isolated SQLite-backed
Mission Control subprocess and talks to it over the same versioned HTTP
API the desktop product uses.
"""
