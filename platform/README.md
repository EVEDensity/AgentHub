# Platform Contracts

`platform/` contains cross-process contracts, capability declarations, and
deployment-neutral platform metadata. It is not a dumping ground for service
implementation details.

Contract changes require:

- an explicit version or backward-compatibility statement;
- producer and consumer inventory;
- migration and rollback behavior;
- contract tests;
- an ADR when ownership or semantics change.

Mission and WorkUnit semantics are defined by the domain and API contracts.
A2A and MCP schemas describe integration boundaries and must map to those
objects without duplicating their lifecycle.
