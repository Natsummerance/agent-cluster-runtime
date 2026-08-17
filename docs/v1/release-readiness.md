# DoAI Workbench v1 release readiness

Status: **alpha — release blocked** (2026-08-17)

This file is the capability/deletion gate for the Cordis dual-plane migration. A checked item means
the replacement path and its tests exist; it does not authorize deleting an unchecked legacy path.

## Implemented and verified

- [x] Canonical JSON schema generates TypeScript, frontend TypeScript, and Pydantic models.
- [x] Cordis Host owns transactional plugin scopes, reversible effects, dependency/provider checks,
  profile/bundle/patch composition, diagnostics, and the five event dispatch semantics.
- [x] Standard Agent uses the durable canonical event store for model context, replay, recovery, fork,
  approvals, tools, and execution-world operations.
- [x] Supervised stdio JSON-RPC connects the Host to the Python Organization Plane with handshake,
  heartbeat, cancellation, mutation metadata, replay, and structured faults.
- [x] Software Company preserves 12 roles and 7 meeting types through the Host/Python integration.
- [x] Standard, Code-Python, Code-TypeScript, Minimal, and Creator presets have contract tests.
- [x] Enterprise features have official plugin manifests and tenant-scoped providers.
- [x] The frontend consumes generated v1 protocol types and has a strict RPC/projection client.
- [x] `doai migrate` supports dry-run, backup, validation, idempotency, atomic writes, and rollback.
- [x] CI has a dedicated v1 core typecheck/test/protocol-freshness gate.

## Blocking v1 release

- [ ] Implement the production Host HTTP/WebSocket transport for `/api/v1/rpc` and session streaming.
- [ ] Move every Workbench store/page from legacy REST/SSE shapes to the generated protocol client.
- [ ] Make `doai run`, `web`, `config`, and `session` operate real profiles instead of failing closed.
- [ ] Bundle the Node Host and Python Organization Plane in Electron installers and exercise installed
  artifacts without system Node/Python.
- [ ] Add OS-grade Code Mode sandbox escape tests; the TypeScript VM/process boundary is not itself a
  production security boundary.
- [ ] Run real Standard and both Code modes against release fixtures, plus migration rollback in an
  installed artifact.
- [ ] Only after all items above pass, remove `src/agent_cluster` runtime paths, legacy EventBus/logs,
  old CLI and duplicate frontend types, then enforce a zero-legacy-import release check.
- [ ] Synchronize all product versions and release metadata to `1.0.0` only after the deletion gate.

## Current regression evidence

- Python: 893 passed, 4 skipped.
- Frontend: 164 passed; production Vite build passed.
- v1 TypeScript packages: 47 passed; all six packages typecheck.
- Protocol and Python Organization Plane: 16 passed.

The branch must remain `1.0.0-alpha.0` while any blocking item is unchecked.
