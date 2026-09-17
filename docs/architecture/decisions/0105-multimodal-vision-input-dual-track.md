# ADR-0105: Multimodal Vision Input via Dual-Track Content Parts

> Status: accepted
> Owner: backend maintainers
> Date: 2026-08-27
> Scope: `app/services/adapter_manager.py`, `services/python/model_adapter_service`,
> attachment-to-prompt pipeline, token budgeting, guardrails
> Design document: [multimodal.md](../components/multimodal.md)

## Context

The application is a pure-text pipeline today, but the ground is not zero:
the frontend already supports picking/pasting/uploading images (≤2 MB inline
data URL), the new-api gateway has been verified to pass standard OpenAI
multimodal request bodies through untouched (Moonshot
`moonshot-v1-8k-vision-preview` identified the repo logo and billed it at
1049 prompt tokens on 2026-08-27), and an offline vision tool package
(`app/services/tools/multimodal/`) already exists.

The remaining gaps are purely application-side:

- Every request-side message construction sends `content: str`
  (`adapter_manager` ~4 construction sites plus the standalone
  `model_adapter_service` schema).
- The frontend splices base64 into the message body as pseudo code fences,
  polluting prompts.
- Token budgeting has no image cost item; embedded base64 was even counted
  as text.
- No routing guard prevents image parts from reaching text-only models.

Industry mapping (LangChain content blocks / langchain-go vision RFC /
forum image-tool pattern, see multimodal.md §3) converges on adopting the
OpenAI `content: str | list[part]` shape as the internal transport standard
instead of inventing another protocol.

## Decision

Adopt **dual-track message content**: `content` remains either a plain
string (backward compatible — every existing call site keeps working
unchanged) or a list of standard OpenAI-style content parts
(`{"type": "text"}` / `{"type": "image_url"}`).

Sub-decisions ratified together (per v3 rollout plan success criteria):

1. **Routing constraint (fail-closed)**: only models registered as
   vision-capable in the capability registry (`capability.py`, default rule
   `*: *vision*` plus explicit patterns and the additive
   `AGENTHUB_VISION_MODELS` env override) may carry image parts. A pure-text
   model receiving an image part raises `VisionUnsupportedError` explicitly
   — never silent degradation; the degrade path is the separate
   `image_describe` structured-description tool.
2. **Budget hard caps**: images are billed conservatively at a fixed
   1024 tokens each (`IMAGE_TOKEN_COST`), ≤4 images per turn and ≤6 MB
   inline total per turn. Vendor-specific image tokenization stays an
   observed metric only — never a promise.
3. **Compaction semantics**: when context compaction summarizes history,
   old-turn image parts do not enter summary prose; they leave a
   "user sent N image(s)" placeholder marker (aligned with Deep Agents).

Anthropic receives converted native blocks (`base64`/`url` sources) from the
same internal parts, so callers keep one format.

### Explicitly out of scope (this slice)

- Sending images to `local_claude`/`cloud_code` CLI adapters.
- Image *generation* (output side).
- Audio/video modalities.

## Alternatives considered (recorded dissent)

- **Invent a custom multimodal protocol** — rejected: every target backend
  (vLLM/Ollama/OpenAI/Kimi/via-gateway) already speaks the OpenAI part
  shape; a custom protocol would add a translation layer for no benefit.
- **Keep splicing base64 into the text body** (status quo of
  `outgoingMessageDraft.ts`) — rejected by reviewers: pollutes prompts,
  makes token accounting meaningless, and is capped at nothing.
- **Make `image_describe` (sub-LLM description) the only path, no protocol
  change** — retained as the degrade path for non-vision models, but rejected
  as the sole path: double LLM latency, loses direct grounding, and blocks
  gateway-native vision channels that already work.

## Consequences

- Adapter layer changes are additive and backward compatible; the full
  existing test matrix must stay green (success criterion of MM-1).
- The frontend attachment pipeline gains a structured path in MM-2; until
  then dual-track alone changes no user-visible behavior.
- Token budgeting must special-case lists (images counted as fixed constants,
  text parts counted normally).
- Compaction and budget utilities gain image-awareness obligations tracked
  under MM-3/MM-5 gates.
