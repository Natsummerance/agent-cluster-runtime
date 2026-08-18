# Scope registry ownership

Status: Implemented

## Decision

Each Host activation owns a sibling logical event scope and a root provider scope. `Context.scope()` creates
paired child registries with an explicit parent link. Exactly-one/optional provider lookup selects the nearest
scope; many-provider lookup aggregates ancestors before local entries. Listener and onion interceptor lookup
also walks ancestors before local registrations, while mutations and disposers touch only the exact entry in
the current scope.

Provider, listener, interceptor, and Code tool registrations are effects on the current Cordis fiber. A failed
shadow therefore finishes rollback before `activate()` rejects, and a successful switch publishes the new
scope before draining the old one. `ToolRuntime.register()` returns an idempotent identity-checked disposer so
an old disposer cannot remove a later same-name registration.

## TDD evidence

RED:

- `pnpm --filter @doai/host exec vitest run tests/host-lifecycle.test.ts` — 2 failed: shadow onion returned
  `active:shadow:terminal`; child override raised `PROVIDER_CONFLICT`.
- `pnpm --filter @doai/agent-runtime exec vitest run tests/tool-runtime.test.ts` — 1 failed:
  `disposeFirst is not a function`.
- `pnpm --filter @doai/presets exec vitest run tests/preset-plugins.e2e.test.ts -t "returns a shared ToolRuntime"`
  — 1 failed: unload left one Code tool above the zero baseline.

GREEN/acceptance:

- `pnpm test:host` and `pnpm typecheck:host`
- `pnpm test:agent` and `pnpm typecheck:agent`
- `pnpm test:presets` and `pnpm typecheck:presets`
- `uv run python scripts/verify_agent_notes.py`

## Remaining boundary

This decision covers in-process Host provider/event registries and Code tool registration only. Permission,
credential, health-check, dependency-epoch, process/handle ownership, transport, frontend, and legacy removal
remain later v1 tasks. Repeated calls to `scope()` intentionally create distinct sibling identities even when
their metadata objects contain equal values.
