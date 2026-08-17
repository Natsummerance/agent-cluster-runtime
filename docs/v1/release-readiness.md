# DoAI Workbench v1 release readiness

Status: **alpha prototype — release and legacy deletion blocked** (2026-08-17)

This file is the capability/deletion gate for the Cordis dual-plane migration. A checked item means
the replacement path and its tests exist; it does not authorize deleting an unchecked legacy path.

## Evidence available

- [x] Canonical JSON schema generates TypeScript, frontend TypeScript, and Pydantic models.
- [x] Cordis Host prototypes profile/bundle/patch composition, diagnostics and five event APIs.
- [x] Standard Agent uses the durable canonical event store for model context, replay, recovery, fork,
  approvals, tools, and execution-world operations.
- [x] Supervised stdio JSON-RPC connects the Host to the Python Organization Plane with handshake,
  heartbeat, basic cancellation, mutation metadata and structured faults.
- [x] An integration smoke test invokes all 12 role IDs and emits all 7 meeting IDs.
- [x] Standard, Code-Python, Code-TypeScript, Minimal, and Creator presets have contract tests.
- [x] Enterprise capability manifests load as ordinary plugins; their implementations are placeholders.
- [x] The frontend consumes generated v1 protocol types and has a strict RPC/projection client.
- [x] `doai migrate` supports dry-run, backup, validation, idempotency, atomic writes, and rollback.
- [x] CI has a dedicated v1 core typecheck/test/protocol-freshness gate.

These checks prove module wiring, not production readiness. The authoritative evidence and execution order are
in `docs/superpowers/handoff/2026-08-17-v1-cordis-continuation.md`.

## P0 contract blockers

- [ ] Isolate providers/listeners/interceptors/tools per scope; shadow activation must be invisible before switch.
- [ ] Enforce manifest/API/version, permission grants, credential probes and health checks during activation.
- [ ] Generate discriminated event payload types and validate vocabulary, owner and state transitions.
- [ ] Replace full-log single-process writes with durable transactional append/batch and strict idempotency digest.
- [ ] Prove exactly-once recovery by killing the real Python peer before, during and after durable commit.

## P1 runtime blockers

- [ ] Replace the synthetic organization loop with a replayable domain state machine and real delivery actions.
- [ ] Migrate task board, usage-based budget, artifacts, memory retrieval and evolution apply/rollback.
- [ ] Propagate cancellation through RPC, model, tool and child-process boundaries.
- [ ] Replace generic enterprise Maps with the mature RBAC/OAuth/audit/calendar/dependency/evolution behavior.
- [ ] Require an OS/container sandbox provider for Code Mode and pass escape/resource-limit tests.

## P2 product blockers

- [ ] Implement the production Host HTTP/WebSocket transport for `/api/v1/rpc` and session streaming.
- [ ] Move every Workbench store/page from legacy REST/SSE shapes to the generated protocol client.
- [ ] Make `doai run`, `web`, `config`, and `session` operate real profiles instead of failing closed.
- [ ] Implement Workbench, Web Server, Headless and Python SDK profiles plus the software-company bundle.
- [ ] Add a Codex MCP facade as a transport adapter without creating another runtime owner.
- [ ] Bundle the Node Host and Python Organization Plane in Electron installers and exercise installed
  artifacts without system Node/Python.

## P3 migration and release blockers

- [ ] Migrate the complete v0.7.2 data tree, including checkpoints, ledger, changes, memory, artifacts and forks.
- [ ] Turn all three benchmark descriptions into executable CI gates with retained evidence.
- [ ] Run real Standard and both Code modes against release fixtures, plus migration rollback in an installed artifact.
- [ ] Verify packaged applications on Windows, macOS and Linux without system Node/Python.
- [ ] Include the pinned license text and verify provenance/license freshness.
- [ ] Only after all items above pass, remove `src/agent_cluster` runtime paths, legacy EventBus/logs,
  old CLI and duplicate frontend types, then enforce a zero-legacy-import release check.
- [ ] Synchronize all product versions and release metadata to `1.0.0` only after the deletion gate.

## Current regression evidence

- Python: 893 passed, 4 skipped.
- Frontend: 164 passed; production Vite build passed.
- v1 TypeScript packages: 47 passed; all six packages typecheck.
- Protocol and Python Organization Plane: 16 passed.

The Organization test uses a deterministic model and automatic approval; Code tests are process-level smoke
tests, not sandbox certification. Legacy Python/frontend results prove regression safety only, not migration.

The branch must remain `1.0.0-alpha.0` while any blocking item is unchecked.
