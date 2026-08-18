# Host activation policy and dependency epochs

- Date: 2026-08-18
- Class: architecture
- Status: implemented

## Decision

Host activation is one fail-closed transaction with this fixed order: validate and snapshot every effective
manifest/config; validate every exact plugin + permission kind + resource grant; probe every authorized opaque
credential handle; start the isolated shadow scope; verify live providers; run health checks against a frozen
candidate-only view; publish the active scope and epoch report atomically; then drain the old scope.

Plugins without permission declarations retain the safe empty-options path. Permission-bearing plugins do not:
callers must provide every exact grant, and each credential grant additionally requires a successful probe. The
probe receives only the opaque resource string. Probe, apply, and health exceptions are replaced with stable
diagnostics that contain no raw `cause`.

Runtime validation keeps the repository's existing identifier vocabulary: plugin/dependency names are lowercase
letters, digits, and hyphens with a leading letter; capabilities additionally allow dots. Permission resources
remain opaque non-empty strings and are not format-restricted.

## Rollback and diagnostics

Candidate plugin effects wrap their disposers so Cordis can attempt all cleanup while Host records each failure as
a deterministic JSON-safe `ROLLBACK_FAILED` snapshot. On a primary apply/provider/health failure, Host awaits the
entire shadow disposal before rejecting. The primary stage code remains top-level and any cleanup failures appear
in the deeply frozen `details.rollbackFailures` array; raw errors, aggregate children, cancellation-like errors,
effect labels, and secret-bearing causes are never retained. Failed candidates do not drain the old scope or
advance any epoch.

## Epoch semantics

Every successful activation restarts the selected scope and advances one Host epoch. Its returned, persisted,
deeply frozen report lists each actually started plugin and both manifest dependency observations and live
capability-provider observations. Provider observations use the actual provider owner, include every live owner
for `many`, and use stable sorting. The old scope drains only after the new active scope and report are published.

## TDD evidence

The initial focused RED groups failed as follows:

- manifest/API/semver: 18 failed (invalid shapes reached activation or surfaced unstable errors);
- exact grants: 10 failed (permission-bearing plugins were allowed without grants);
- credentials: 4 failed (no probe was performed and failures were not diagnosed);
- ordinary health rollback: 5 failed (health checks were ignored);
- primary plus rollback failure: 5 failed (rollback failures were lost and raw primary data remained);
- dependency epoch/restart: 2 failed (activation returned no report).

GREEN: `pnpm --filter @doai/host exec vitest run tests/activation-policy.test.ts`,
`pnpm typecheck:host`, and `pnpm test:host`. Public signature/fail-closed caller acceptance also covers
agent-runtime, presets/Creator, organization-bridge, and enterprise-plugins test and typecheck commands.

## Boundaries

This decision does not add typed event envelopes, a durable event store, crash/replay policy, frontend behavior,
or legacy deletion. Cleanup failures while draining an already committed old scope are isolated from the new
activation result; this task's composite rollback contract applies to failure of the uncommitted candidate.
Concurrent overlapping calls to `activate()` are not scheduled by this change and remain a caller coordination
boundary.
