# Durable model-request tool surface

- Date: 2026-08-18
- Class: testing
- Status: implemented

## Decision

Every `StandardAgent` model call now follows one transaction: project messages from the current durable event
prefix; call `ToolRuntime.list()` exactly once; validate, detach, and recursively freeze that ordered tool surface;
await `model.requested { step, tools }` persistence; validate the actual event returned by that append; then pass
the same current captured tool values to the model adapter only when the returned durable surface is semantically
identical. This returned-event check matters for a partial idempotent retry, where `appendIdempotent()` may return
the event committed by the first attempt. A matching retry may continue; an absent, malformed, or different
durable surface fails before the adapter and cannot replay either the old/unloaded surface or the new registry
surface under contradictory durable history. An append failure keeps the adapter uncalled. Adapter rejection after
a committed request retains the existing `model.failed` behavior.

Equality includes tool/array order and every record's own-key order as observed by JSON serialization. Two schemas
with the same key/value set but a different insertion order are not the same model request surface and therefore
fail closed on an idempotent retry.

Messages are not copied into `model.requested`. Their sole durable reconstruction remains
`projectModelMessages(events.filter(event => event.seq < requested.seq))`. Tools are read only from the matching
requested event, so complete `{messages, tools}` reconstruction no longer depends on a live registry or an adapter
capture.

## Lossless JSON boundary

The capture accepts only JSON primitives (`null`, boolean, string, ordinary finite number), dense intrinsic arrays,
and plain/null-prototype records with enumerable own data string keys. It preserves insertion/index order, special
own keys through inert null-prototype records, shared acyclic subgraphs by structural duplication, and complete
nested schemas. It rejects `undefined`, bigint, function, symbol, non-finite numbers, `-0`, sparse/extended arrays,
accessors, symbol/non-enumerable keys, cycles, descriptor/proxy failures, and non-plain objects. Every captured
array, tool entry, schema record, and nested child is frozen; adapter writes fail and cannot affect the durable
event or registry. A later valid registry change appears only in the next call's capture.

The model-visible outer shape is also checked at runtime: the surface must be an array; every item must contain
exactly string `name`, string `description`, and record `input_schema`. This shape check applies both to the current
registry capture and the returned durable event, after descriptor-safe JSON detachment.

Invalid surfaces raise internal `ModelRequestToolSurfaceError` with code `MODEL_REQUEST_TOOLS_INVALID`. Public
fields are limited to a safe message and pointer containing trusted `/tools/<index>/name|description|input_schema`
segments, numeric array indices, and the literal `<property>` for attacker-controlled record keys. Raw values,
property names, getter/proxy errors, and causes are discarded. The existing outer flow may persist that safe message
in `agent.failed`; it must not persist `model.requested`, `model.completed`, or `model.failed` because no adapter call
began.

An idempotent returned-event absence, malformed shape, JSON-loss value, or semantic mismatch uses the same internal
error name with code `MODEL_REQUEST_TOOLS_DURABLE_MISMATCH`, pointer `/tools`, and the fixed message
`durable model request tools do not match current tool surface`. No current/durable tool name, schema value,
descriptor error, or cause is exposed. The previously committed requested event remains authoritative, no model
event follows it, and the existing outer flow appends only the safe `agent.failed` message.

## TDD evidence

Focused RED on base `8cbf3963b03e62d5d7375b8c17eb53c4e1e55bd7`:

```text
pnpm --filter @doai/agent-runtime exec vitest run tests/model-visible-request-contract.test.ts
13 failed
```

Two real invocations had equal durable message prefixes and different adapter tools, but the first durable equality
failed with `model.requested.payload.tools` received as `undefined`. The lifecycle case showed adapter mutation was
not blocked, and all eleven JSON-loss cases reached the adapter and returned success. This was the H2 blocker, not
a snapshot mismatch.

GREEN uses the same focused real `StandardAgent.invoke()` path. Tests cover per-call durable reconstruction,
two-step disposal, nested mutation isolation, complete ordered schemas, JSON round-trip, special/null-prototype and
shared-DAG data, append barrier/failure, model failure, recursive redaction, and the JSON-loss families. No H2
snapshot was added or updated.

Review-fix RED on `85182d2` added real partial-idempotent retry and required-shape cases. The focused result was
`6 failed | 23 passed (29)`: a mismatching, missing, or malformed returned durable surface still entered the model,
and non-string name/description or non-record input schema also entered it. The same-surface retry already passed,
pinning compatibility. GREEN is `29 passed (29)` and additionally proves one list call, zero model calls on mismatch,
unchanged prior requested data, safe `agent.failed`, and normal completion for a semantically matching retry.
An additional review RED then showed `1 failed | 29 passed (30)` because reordered schema keys still entered the
adapter; ordered record-key comparison made the focused suite GREEN at `30 passed (30)`.

## Boundary

This runtime fix only makes the current registered tool surface durable. It does not define hidden-tool policy,
change tool visibility, alter message projection/session storage/protocol vocabulary, or complete H2 snapshots.
Control returns to test-only H2 after independent review.
