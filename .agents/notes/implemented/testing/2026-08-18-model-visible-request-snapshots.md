# Model-visible StandardAgent request snapshots

- Date: 2026-08-18
- Class: testing
- Status: implemented

## Contract

The existing repository-repair vertical fixture now synchronously
`structuredClone()`s `{ messages, tools }` inside every real
`ModelProvider.generate(request)` call. `signal` is deliberately excluded
because it is transport control, not model-visible context. The two raw
recordings are compared before normalization against an oracle derived only
from the public `SessionEventStore.read('repair')` result:

- messages are `projectModelMessages(events.filter(event => event.seq < requested.seq))`;
- tools are validated and losslessly detached from that same
  `model.requested.payload.tools` value.

The test never re-queries `ToolRuntime`, substitutes fixture schemas, reads
store internals, or uses an adapter capture as expected data. It asserts raw
structural equality plus recursive `Object.keys()` order, so reordered schema
keys, tools, messages, calls, required arrays, or nested constraints fail before
snapshot presentation.

## Snapshot and lifecycle surface

The single reviewable file snapshot contains one structured block per real
request and a separate count invariant proves:

```text
recording count = durable model.requested count = snapshot request count = 2
```

Both blocks retain exact system/user/assistant/system/tool message order,
multi-call IDs and arguments, matching tool-result IDs/names/content, approval
messages, exact tool insertion order, descriptions, and complete recursive
schemas. A test-local snapshot serializer sets only `compareKeys: null` so the
visible snapshot preserves insertion order instead of pretty-printer key
sorting; it does not transform the value.

The three workspace tools are active exactly once in every request. A nested
`diagnostic.transient` tool is registered for request 0 and disposed inside the
first adapter call, so request 1 and all later captures omit it while request 0
remains unchanged. `diagnostic.never-selected` is constructed but never
registered and is absent from every request. These are the only observable
unselected/unloaded meanings: the runtime has no distinct policy-hidden
registration state, and this test does not claim one.

## Presentation normalization

The fixture uses deterministic text, timestamps outside the model request, and
deterministic call IDs (`fix`, `verify`), so none of those fields are rewritten.
The only actual dynamic value is `process.execPath`. The field-level normalizer
changes exactly:

```text
requests/1/messages/2/tool_calls/1/arguments/argv/0
  -> <NODE_EXECUTABLE>
```

The test asserts that exact one-item replacement path. It deep-clones before
normalizing, never sorts, and never touches tools/schema. Its self-test proves
the original raw capture is unchanged, the same executable token in a schema
`const` remains verbatim, an occurrence in system content fails review, and
recursive own-key reordering fails the raw equality helper.

## TDD evidence

Genuine RED on reviewed base
`038bbe831e7124e5c1cbd6bc381afca126960dc0`, with no accepted snapshot:

```text
$env:CI = 'true'
pnpm --filter @doai/agent-runtime exec vitest run tests/agent-loop.test.ts

Test Files  1 failed (1)
Tests       1 failed | 4 passed (5)
Snapshots   1 failed
```

The sole failure was the missing/mismatched new per-request snapshot. All raw
durable-oracle, count, lifecycle, normalization, and order assertions had
already passed, so no runtime defect was hidden by snapshot acceptance.

The snapshot was generated explicitly with:

```text
pnpm --filter @doai/agent-runtime exec vitest run tests/agent-loop.test.ts -u
Snapshots  1 written
Tests      5 passed (5)
```

The complete file was then inspected for every message, schema field/order,
tool lifecycle change, sentinel, and accidental temporary path/sensitive value.
A no-update rerun passed `5 passed (5)`; `pnpm typecheck:agent` also passed.

## Evidence boundary

This freezes the real StandardAgent repair request path at H2 and detects its
future model-visible drift. It does not define a hidden-tool policy, exercise
all possible agents/providers, or alter runtime/protocol/store/replay behavior.
