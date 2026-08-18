# Durable model-request tool surface

- Date: 2026-08-18
- Class: testing
- Status: implemented

## Decision

Every `StandardAgent` model call now follows one transaction: project messages from the current durable event
prefix; call `ToolRuntime.list()` exactly once; validate, detach, and recursively freeze that ordered tool surface;
await `model.requested { step, tools }` persistence; then pass the same captured tool values to the model adapter.
An append failure keeps the adapter uncalled. Adapter rejection after a committed request retains the existing
`model.failed` behavior.

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

Invalid surfaces raise internal `ModelRequestToolSurfaceError` with code `MODEL_REQUEST_TOOLS_INVALID`. Public
fields are limited to a safe message and pointer containing trusted `/tools/<index>/name|description|input_schema`
segments, numeric array indices, and the literal `<property>` for attacker-controlled record keys. Raw values,
property names, getter/proxy errors, and causes are discarded. The existing outer flow may persist that safe message
in `agent.failed`; it must not persist `model.requested`, `model.completed`, or `model.failed` because no adapter call
began.

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

## Boundary

This runtime fix only makes the current registered tool surface durable. It does not define hidden-tool policy,
change tool visibility, alter message projection/session storage/protocol vocabulary, or complete H2 snapshots.
Control returns to test-only H2 after independent review.
