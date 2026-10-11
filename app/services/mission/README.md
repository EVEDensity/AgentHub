# Mission Control services

`MissionService` composes application commands over the Mission repository.
Mission, Contract, WorkUnit, leases, execution checkpoints, Artifact metadata,
Evidence, and Decisions remain the durable sources of truth. Mixins do not own
parallel state or execute model loops.

## Chat dispatch

`_chat_dispatch_mixin.py` admits one enabled `function-calling` Agent from the
workspace catalog and creates one `desktop.task` root for a started chat Mission.
The command locks the exact Mission, checks its workspace and source, rechecks
the catalog against the admitted participant snapshot, then persists the PENDING
WorkUnit and creation event in one transaction. Repeated admission returns the
same plan; it never rebinds existing work or creates a new attempt.

Catalog capability tags are descriptive. They are not copied into Contract tool
grants. The current chat Contract grants no tools and retains manual acceptance.
Dispatch alone cannot create PASS Evidence or mark a Mission successful.

Workspace claims admit chat roots only for the exact `chat` / `desktop.task` /
`function-calling` tuple, assigned Agent, workspace, supported kind, and lease
owner. The desktop resolver preserves the chat source in bounded model context.
The built-in desktop process consumes only `local-desktop-agent`; another Agent's
unit remains PENDING until a Runner configured for that binding claims it.

Multi-executor chat workflows and outbound A2A chat routes are not implemented.
The adapter rejects those requests instead of redirecting them to the built-in
Agent. See ADR-0112 and `tests/api/test_chat_mission.py` for admission, SQLite,
transaction rollback, claim, and lease-fencing coverage.
