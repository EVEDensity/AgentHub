# ADR-0075: Controlled Mission Fork Ancestry

> Status: accepted  
> Owner: Mission Control maintainers  
> Date: 2026-08-16  
> Scope: Mission derivation, checkpoint ancestry, and reusable Artifact inputs

## Context

ADR-0074 introduced durable, content-minimized ExecutionCheckpoints as ancestry
anchors rather than resumable Harness images. A derived Mission needs explicit
provenance and reusable inputs without copying model state, bypassing
verification, or rebinding the source Mission and Contract.

## Decision

Mission Control provides one controlled human command for Mission fork. The
command is same-workspace only and must identify a terminal successful
ExecutionCheckpoint plus one or more registered ArtifactRefs from that exact
source WorkUnit attempt. The source WorkUnit must have independently reached
`SUCCEEDED`. Mission Control verifies Artifact byte availability and integrity,
then repeats source identity and metadata validation inside the creation
transaction.

The command creates a new `READY` Mission and a new `PENDING` root WorkUnit. It
reuses the source Mission's exact immutable Contract ID and version. ArtifactRefs
become inputs of the new WorkUnit; Evidence, checkpoints, leases, attempts,
Decisions, and mutable execution state are never copied.

The derived Mission uses source type `mission.fork`. Its `reference` is the
source Mission ID and its `externalId` is the source ExecutionCheckpoint ID.
The checkpoint supplies the source WorkUnit and attempt identity, avoiding a
second ancestry record or free-form metadata. Both fields are mandatory for
this source type.

## Consequences

A fork is a new unit of intent and execution, not a retry or resume operation.
It can be authorized, budgeted, cancelled, verified, and audited independently
while retaining a deterministic path to its source attempt and Artifact inputs.

Fork creation fails without mutation when the checkpoint is missing, failed,
non-terminal, belongs to another Mission, no longer matches the successful
WorkUnit attempt, or any Artifact reference or byte observation is invalid.
Contract revisions created after the source Mission do not silently rebind the
fork.

The v1 Mission schema change is backward compatible: it adds one source enum
value and applies new required fields only to that value. Existing Mission
documents and protocol adapters are unchanged.

## Alternatives considered

- Resume the source Harness from a checkpoint: rejected because checkpoints do
  not contain portable model, tool, or sandbox state.
- Copy source Evidence and mark the fork successful: rejected because Evidence
  proves criteria only for its original Mission and WorkUnit attempt.
- Store ancestry only in an event payload: rejected because Mission source is
  the durable, queryable origin contract and the root WorkUnit owns inputs.
- Bind the fork to the latest Contract revision: rejected because that changes
  permissions, budgets, and acceptance criteria without an explicit decision.

## Verification

Domain and JSON Schema tests require complete fork provenance. Service and API
tests must cover same-workspace authorization, successful checkpoint and
WorkUnit admission, exact-attempt Artifact closure, byte verification,
transactional creation, replay/conflict behavior, and absence of partial writes.
Integration tests must prove the new root can be claimed and starts at attempt
one without accessing source leases or checkpoints.
