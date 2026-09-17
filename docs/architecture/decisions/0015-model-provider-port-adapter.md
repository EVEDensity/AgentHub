# ADR-0015: Model Provider Port Adapter

> Status: accepted
> Owner: architecture maintainers
> Date: 2026-08-08
> Scope: Harness provider integration and response normalization

## Context

ADR-0014 introduced a provider-independent `ModelPort`, but no real adapter
could connect the Harness to the existing OpenAI-compatible and other stateless
prompt adapters. Passing provider-specific JSON through the Harness would make
the loop depend on one API shape and would reintroduce legacy session concerns.

## Decision

Add `ModelAdapterPort`, a stateless adapter around the existing prompt-adapter
interface. It binds provider configuration at construction and, for each
request, forwards the WorkUnit instruction and accumulated `FunctionResult`
feedback. It normalizes plain text, internal `tool_calls` JSON, and
OpenAI-compatible `choices[].message` responses into the Harness
`ModelResponse` contract.

Malformed JSON remains plain text. Malformed tool arguments are represented as
an object carrying the raw value so the Harness validator can return structured
feedback rather than silently invoking a tool. Non-text provider responses and
malformed tool entries are rejected or ignored according to the normalized
contract. API keys and provider configuration remain adapter-owned and are not
persisted by Harness or Runner.

## Consequences

Existing provider implementations can be used by the new Harness without
changing Runner or Mission Control. Provider-specific response parsing is
centralized and testable. The current feedback context is serialized into the
next prompt and remains request-scoped; durable conversation history,
streaming, usage accounting, retries, and model routing are deliberately later
capabilities.

## Alternatives considered

- Parse provider JSON inside `FunctionCallingHarness`: rejected because it
  couples loop policy to transport formats.
- Reuse AgentService's full session loop: rejected because it owns legacy
  session/task state and has broader fallback behavior.
- Let providers write checkpoints or WorkUnit status: rejected because only
  Mission Control owns durable lifecycle state.

## Verification

- Adapter tests cover forwarding, tool-result rendering, plain text,
  internal/OpenAI tool-call normalization, and malformed argument preservation.
- Existing Harness, Runner, Mission, Artifact, persistence, and A2A tests remain
  green.

## Supersedes

None. This implements the provider edge for ADR-0014.
