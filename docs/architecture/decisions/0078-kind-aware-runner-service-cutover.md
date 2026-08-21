# ADR-0078: Select Kind-Aware Model Execution in the Runner Service

> Status: accepted  
> Date: 2026-08-21  
> Owners: execution maintainers

## Context

ADR-0077 established a mixed `a2a.inbound` and `mission.fork` workspace
composition with one resolver registry for both claim capability declaration
and dispatch. The deployable Python Runner service still selected the
inbound-only builder, leaving the verified composition unreachable in its
intended process boundary.

## Decision

`build_runner_runtime` selects the kind-aware model workspace builder. Its
registered kinds are exactly `a2a.inbound` and `mission.fork`; no environment
setting may add or remove them. The existing explicit-Mission fork builder is
retained for its narrow integration surface.

`a2a.delegate` remains outside this runtime because outbound A2A uses a native
transport supervisor rather than the model Harness. The service configuration
continues to reject `a2a.outbound`, and Gateway direct dispatch removal remains
a separate atomic cutover.

## Consequences

- A deployed model Runner can claim either registered model-backed root for its
  configured Agent/adapter binding.
- Claim declarations stay coupled to executable resolver registrations.
- Operators cannot opt into unsupported kinds through process configuration.
- Outbound deployment, credentials, and remote transport ownership do not
  enter this change.

## Verification

Runtime composition tests require the service to select the kind-aware builder.
The mixed-kind ASGI gate proves both kinds preserve their respective context,
checkpoint, Artifact, and completion path. Existing configuration tests require
`a2a.outbound` rejection.
