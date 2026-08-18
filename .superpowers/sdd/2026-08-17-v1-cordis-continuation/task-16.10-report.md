# Task 16.10 Report — Scope 隔离回归测试与修复

## Status

Complete. Implemented only Task 16.10 Host scope/registry ownership and Code tool disposer behavior. No
frontend, legacy deletion, or Task 16.11+ work is included.

The requested `.agents/lessons/02-testing-strategy.md` and
`.agents/lessons/07-debugging-workflow.md` do not exist in this worktree. I read the repository equivalents
`docs/lessons/02-testing.md` and `docs/lessons/07-debugging.md`, as confirmed by the review rubric, plus
`AGENTS.md`, the brief, `docs/lessons/README.md`, `docs/lessons/01-environment.md`, the relevant runtime
contract, and the Task 16.10 handoff section.

## Implementation

- Host activation event scopes are siblings. A shadow cannot observe or mutate active listeners/interceptors;
  failed activation awaits fiber rollback before rejecting, and successful replacement drains the old scope
  only after publishing the new one.
- `Context.scope()` now creates child provider and event registries. Exactly-one/optional providers use the
  nearest non-empty scope; `many` providers aggregate parent-to-child in stable order. Listeners and onion
  interceptors inherit parent-to-child and never leak upward or across siblings.
- Provider/listener/interceptor mutations are entry-identity disposers owned by the current Cordis fiber.
- `ToolRuntime.register()` now returns an idempotent entry-identity disposer. Ignoring it remains compatible;
  duplicate/unknown tool errors remain fail-loud.
- Code tool plugins wrap registration in `ctx.effect`, so deactivate, replacement, failed startup, and host
  disposal restore a shared runtime without removing unrelated baseline tools.

## Files

- `packages/host/src/host.ts`
- `packages/host/src/events.ts`
- `packages/host/tests/host-lifecycle.test.ts`
- `packages/host/tests/events.test.ts`
- `packages/agent-runtime/src/tools.ts`
- `packages/agent-runtime/tests/tool-runtime.test.ts`
- `packages/presets/src/plugins.ts`
- `packages/presets/tests/preset-plugins.e2e.test.ts`
- `.agents/notes/implemented/architecture/2026-08-18-scope-registry-ownership.md`
- `.superpowers/sdd/2026-08-17-v1-cordis-continuation/task-16.10-report.md`

## RED evidence

All failures were captured before implementation changes.

1. `pnpm --filter @doai/host exec vitest run tests/host-lifecycle.test.ts`
   - `2 failed | 7 passed`
   - shadow isolation: expected `active:terminal`, received `active:shadow:terminal`
   - child override: `HostDiagnosticError: capability requires exactly one provider: storage`
2. `pnpm --filter @doai/agent-runtime exec vitest run tests/tool-runtime.test.ts`
   - `1 failed`
   - `TypeError: disposeFirst is not a function`
3. `pnpm --filter @doai/presets exec vitest run tests/preset-plugins.e2e.test.ts -t "returns a shared ToolRuntime"`
   - `1 failed | 4 skipped`
   - after first deactivate: expected tool count `0`, received `1`

## GREEN evidence

Focused commands:

- `pnpm --filter @doai/host exec vitest run tests/host-lifecycle.test.ts tests/events.test.ts`
- `pnpm --filter @doai/agent-runtime exec vitest run tests/tool-runtime.test.ts`
- `pnpm --filter @doai/presets exec vitest run tests/preset-plugins.e2e.test.ts -t "returns a shared ToolRuntime"`

Related package acceptance:

- `pnpm typecheck:host`
- `pnpm test:host`
- `pnpm typecheck:agent`
- `pnpm test:agent`
- `pnpm typecheck:presets`
- `pnpm test:presets`
- `uv run python scripts/verify_agent_notes.py`

Final fresh results after the last code/test and Agent Note edit:

- Host focused: `2 files passed`, `16 tests passed`.
- ToolRuntime focused: `1 file passed`, `1 test passed`.
- Code 100-cycle focused: `1 passed | 4 skipped`; the target test completed all 100 cycles.
- Host package: typecheck passed; `3 files passed`, `18 tests passed`.
- Agent Runtime package: typecheck passed; `5 files passed`, `13 tests passed`.
- Presets package: typecheck passed; `4 files passed`, `13 tests passed`.
- Agent Notes: `agent notes tree OK: .agents\\notes`.
- `git diff --check`: passed (only expected autocrlf informational warnings).

## Self-review against rubric

- S1–S3: same-name active/failing-shadow listener/interceptor/provider tests cover pre-commit visibility and
  post-reject rollback through real dispatch/lookup; epoch/scope and old behavior remain unchanged.
- P1–P2: child override, sibling fallback, manual disposer fallback, and parent stability use real resolution.
- I1/L1: parent→child listener/onion order and child non-leakage to parent/sibling are fixed by scope-chain
  tests; existing broadcast/parallel/serial/first/onion semantics and fiber cleanup remain covered.
- T1–T2: stale disposer identity, double dispose, duplicate/unknown fail-loud behavior, and 100 consecutive
  activate/deactivate cycles on one Host/shared runtime are covered. Every cycle executes the Code tool and
  verifies the baseline tool remains executable.
- E1–E2: all four touched registration kinds are current-fiber effects; failed and successful shadow paths are
  covered, including old cleanup exactly once.
- A1/R1: SPI entries and `PluginManifest + apply(context, config)` are unchanged; diff is limited to Host,
  agent-runtime, presets, focused tests, this Note, and this report.

## Concerns / remaining boundary

No blocking concern. Task 16.10 does not expose logical scope identity/epoch as a new public object; isolation
identity is internal to the paired registry nodes and Host epoch remains exposed by `inspect()`. Permission,
credential, health, dependency epoch, external process/handle ownership, frontend, and legacy removal remain
explicitly out of scope.
