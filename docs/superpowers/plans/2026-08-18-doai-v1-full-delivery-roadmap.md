# DoAI v1 full delivery master roadmap

Status: **active execution source** for all remaining v1 work, authored 2026-08-18.

## Authoring baseline and evidence truth

- Authoring branch / HEAD: `v1-cordis-dual-plane` / `65cbcbcc42504ae2c32a78a521edb740a5fcf815`.
  This is the input baseline, not the HEAD created by this document's own commit.
- Complete evidence: Task 16.10 is complete; Task 16.9a and its same-mask ordering fix are complete.
- Task 16.11 RED：已存在、未跟踪、未提交、未完成。`packages/host/tests/activation-policy.test.ts`
  is input to the next task and must not enter this roadmap commit; its existence 不得作为完成证据.
- Upstream reference: DeepSeek Harness `dsh-v0.1.0-rc.7` at
  `99f6f02fecdb7dff40c3fbc9470f5907c29f74ca`; Cordis remains `4.0.1`.
- Product truth: the repository is a `1.0.0-alpha.0` prototype. v0.7.2 remains the production path;
  release and legacy deletion are blocked.
- Evidence levels are distinct: fake proves a local contract; integration proves component wiring; real E2E
  proves real peer/process behavior; installed artifact proves packaged behavior without source-tree runtimes.
  Existing legacy regression counts and smoke tests do not prove v1 delivery.

This roadmap is the one active execution source. The [current handoff](../handoff/2026-08-17-v1-cordis-continuation.md)
is evidence and current-state truth; `release-readiness` is the release/deletion gate. The
[rc.7 sync plan](2026-08-18-dsh-rc7-sync-implementation.md) supplies rc.7 专项细节 and provenance, not a
second master roadmap. In short: handoff 是现状与证据；release-readiness 是发布与删除门禁。

## Execution DAG and parallel boundaries

| Stage | Non-parallel spine | Lanes allowed only after the listed spine gate |
|---|---|---|
| P0 | `H1 → H2 → H3 → H4A → H4B → H5` | None. Each package must be GREEN, committed and reviewed before the next starts. |
| P1 | durable domain/runtime foundations | After H5: O1–O7, then O8; E1–E9 after their security/domain owners; S1; C1→C4; J1→J3. Independent ready lanes may run in parallel. |
| P2 | product entry points | After their named P1 owners: P1→P2, T1→T2→T3, K1, U1, D1, S2→S3 may run in parallel where their dependency sets are GREEN. |
| P3 | proof, deletion, release | M1/B1/A1/G1 may overlap only where dependencies permit. L1–L5 wait for replacement evidence; L6 waits for all deletion packages; R1 is last. |

Hard safety boundaries: rich content waits for typed vocabulary, durable event/artifact truth and crash recovery;
durable Jobs wait for durable store/crash, cancellation and process-tree ownership; remote settings wait for
Host-local ownership, transport, tenant/RBAC, redaction and CAS; installed artifacts wait for profiles, transport,
Python supervision and packaging ownership. Legacy deletion/version/tag/release never run in parallel with the
replacement evidence that authorizes them.

Coverage registry: P1 owns `Organization durable state machine`, `token + cost`, `end-to-end cancellation`,
`OS/container sandbox`, `rich content/attachments/MCP/ACP`, and `durable product Jobs`. P2 owns
`Workbench / Web Server / Headless / Python SDK`, `software-company`, `Host HTTP/WebSocket/MCP transport`,
`run / web / plugin / config / session / doctor / migrate`, `React generated client/event projections`,
`Electron Host→Python supervision`, `plugin settings`, and `Codex MCP facade`. P3 owns
`lossless full data tree migration`, `three benchmark runners/golden/CI`,
`Windows/macOS/Linux installed artifacts`, and `optional dependency/release graph/notices/SBOM`.

## Controller and review protocol

For every package the 主智能体 (main controller) freezes ID, brief, base/head and allowed scope, then assigns one
fixed implementer subagent and one fixed read-only reviewer subagent. The fixed implementer owns RED → GREEN → focused/required gates →
one local commit and does not push. The read-only reviewer independently checks the frozen diff and emits separate
Spec and Quality/provenance verdicts with evidence; the developer report is not sufficient evidence. This is
review before push: only dual PASS lets the controller push or advance the DAG.

Critical/Important findings return to the same implementer and the same reviewer for a narrow fix/review cycle,
最多 5 轮. Round five still failing is a blocker: stop without changing ID, bypassing the reviewer or lowering
severity. Minor findings remain recorded with a disposition. Every round retains base/head, diff package, report
and Agent Note evidence.

## P0 — Host and durable protocol foundation

### H1 — Task 16.11 activation policy

- Depends on: completed Task 16.10 only; immediate, non-parallel spine step.
- Allowed/forbidden scope: Host activation policy/tests; forbid frontend, legacy and Task 16.11a+ behavior.
- RED: consume the existing untracked RED intent for manifest/API/semver, post-registration manifest mutation, exact grant ownership, all-grants-before-any-credential-probe, shadow health, dependency epoch, recursive cause redaction, deep-readonly epoch report with the real provider owner, and primary+rollback dual-failure preservation; failed activation must not advance Host/plugin/provider epochs or leak registrations.
- GREEN: minimally snapshot and validate runtime manifests, activate shadow state, probe only after every grant passes, switch atomically, return a recursively immutable report mapped to the true provider owner, and restore that owner on rollback.
- Acceptance: current `pnpm test:host`; current `pnpm typecheck:host`; the activation RED becomes GREEN without broadening scope.
- Commit boundary: activation policy implementation/tests are one reviewable commit; no snapshot or event vocabulary.
- Rollback/blocker: retain the previous epoch/provider graph; any credential leak, residue, replacement disposal or epoch advance blocks H2.
- Definition of complete: all activation/rollback cases pass and independent Spec/Quality review is PASS.

### H2 — Task 16.11a model-visible and tool-schema snapshot

- Depends on: H1 GREEN, committed and reviewed; non-parallel spine step.
- Allowed/forbidden scope: test-only Host/Agent observation of real model calls; forbid runtime fixes, replay/store and frontend.
- RED: capture each real model request's ordered messages plus complete tool schemas and fail if hidden/inactive tools or ordering drift are visible.
- GREEN: no runtime implementation; preserve the failing snapshot as evidence and, if it exposes a real defect, stop and open a separately authorized runtime fix.
- Acceptance: current `pnpm test:host`; current `pnpm test:agent`; current Host/Agent typechecks.
- Commit boundary: snapshot fixtures/assertions only in an independent test-only commit.
- Rollback/blocker: revert only the snapshot commit; any runtime defect blocks H3 until separately fixed and reviewed.
- Definition of complete: every real request has an ordered message/tool-schema snapshot and no production file changed.

### H3 — Task 16.12 generated typed event/content/replay-envelope vocabulary

- Depends on: H2 GREEN, committed and reviewed; non-parallel spine step.
- Allowed/forbidden scope: canonical schema, generator, generated types and focused consumers; forbid durable store, replay keep/drop mask, attachments and legacy deletion.
- RED: reject unknown owner/type/payload/state/ignorable combinations and unversioned replay envelopes; prove generated discriminated payload/content types are stale first.
- GREEN: generate the discriminated event vocabulary, content blocks and versioned replay envelope while durable content remains authoritative.
- Acceptance: current `uv run python scripts/generate_protocol.py --check`; current `pnpm test:host`; current `pnpm test:agent`; current typechecks.
- Commit boundary: schema, generator, generated outputs and direct contract tests form one vocabulary commit.
- Rollback/blocker: rollback all generated outputs with their schema; any hand-written parallel type or replay/store behavior blocks H4A.
- Definition of complete: every durable/model-visible event has one generated typed contract and durable content is the sole truth.

### H4A — Task 16.13A durable batch store and cursor

- Depends on: H3 GREEN, committed and reviewed; non-parallel spine step.
- Allowed/forbidden scope: Agent durable event store, writer/lock/cursor and corruption tests; forbid max-token replay alignment and product migration.
- RED: fail append transaction/batch atomicity, strict request digest, durable mutation result, resume cursor, concurrent writer/lock, Windows replace plus parent-directory durability, and structural corruption codes.
- GREEN: implement one append/batch transaction owner with durable results/cursors and typed structural corruption; no replay mask.
- Acceptance: current `pnpm test:agent`; current `pnpm typecheck:agent`; `to be introduced by H4A: pnpm --filter @doai/agent-runtime test:durability`.
- Commit boundary: durable storage primitives/tests are one commit; platform durability follow-up may be a separately reviewed H4A fix commit.
- Rollback/blocker: keep old data readable and never partially acknowledge; any torn write, digest alias or ambiguous corruption blocks H4B.
- Definition of complete: acknowledged batches and cursors survive restart with one writer truth and deterministic faults.

### H4B — Task 16.13B replay same-mask and safe degradation

- Depends on: H4A GREEN, committed and reviewed; non-parallel spine step.
- Allowed/forbidden scope: Agent max-token assembler/replay projection and tests; forbid store redesign, attachments and legacy deletion.
- RED: prove content/replay divergence under truncation and unsafe handling of legacy, foreign, malformed or misaligned replay envelopes.
- GREEN: compute one keep/drop mask for content and replay; degrade invalid envelopes to durable content without inventing messages.
- Acceptance: current `pnpm test:agent`; current `pnpm typecheck:agent`; focused same-mask tests required.
- Commit boundary: replay alignment and degradation tests/implementation are one commit, separate from H3/H4A.
- Rollback/blocker: fall back to durable content; any divergence, data loss or acceptance of misalignment blocks H5.
- Definition of complete: valid replay mirrors content under the identical mask and every invalid envelope safely uses durable content.

### H5 — Task 16.14 real crash matrix

- Depends on: H4A, H4B GREEN, committed and reviewed; final non-parallel P0 spine step.
- Allowed/forbidden scope: real Host↔Python peer crash/restart harness and recovery fixes; FakeHost-only evidence and product/legacy work forbidden.
- RED: kill the real Python peer before commit, during durable commit and after commit; expose duplicate/missing model, tool, approval or domain events, cursor drift and orphan processes.
- GREEN: restart from durable cursor, reconcile exactly once and reap the complete process tree without weakening fault semantics.
- Acceptance: current `pnpm test:host`; current `pnpm test:agent`; current `pnpm test:organization`; `to be introduced by H5: pnpm test:crash-matrix`.
- Commit boundary: harness/fixtures first, then each minimal recovery defect as a separately reviewable H5 commit.
- Rollback/blocker: preserve durable log and killed-peer evidence; any FakeHost substitution, duplicate side effect or orphan blocks P1/P2.
- Definition of complete: all three real crash points recover exactly once with correct cursor and zero orphan descendants.

## P1 — Dual-plane runtime and product semantics

### O1 — Organization durable state machine

- Depends on: H5; may start alongside independent E/S/C lanes after P0.
- Allowed/forbidden scope: Organization domain events, reducer, durable intent/result and recovery tests; forbid UI and synthetic-loop compatibility as new truth.
- RED: crash between durable intent/action/result and prove the synthetic loop cannot reconstruct deterministic state.
- GREEN: make one replayable event-driven state machine whose external actions are bounded by durable intent/result.
- Acceptance: current `pnpm test:organization`; current `pnpm typecheck:organization`; real-peer recovery integration required.
- Commit boundary: event model/reducer first, action executor/recovery in narrow O1 commits.
- Rollback/blocker: leave v0.7.2 production path intact; nondeterministic replay or unbounded side effects block O2–O8.
- Definition of complete: identical durable history yields identical Organization state and resumable actions.

### O2 — Real task board

- Depends on: O1.
- Allowed/forbidden scope: Organization task/project aggregate and API contracts; forbid React migration and generic Map persistence.
- RED: prove assignment/dependency/status transitions and replay are absent or non-durable across restart.
- GREEN: port v0.7.2 task-board semantics into O1 events with tenant/project ownership and revision checks.
- Acceptance: current `pnpm test:organization`; current organization typecheck; legacy semantic differential fixtures.
- Commit boundary: task aggregate, commands and projections are one domain commit.
- Rollback/blocker: retain legacy read path; invalid transition, cross-tenant visibility or replay drift blocks P2.
- Definition of complete: real board mutations are durable, authorized, replayable and semantically compared to v0.7.2.

### O3 — Usage token + cost budget

- Depends on: O1; integrates with O2 actions.
- Allowed/forbidden scope: usage ledger/reservation/release policy in Organization/Agent boundary; forbid estimate-only budgets and UI.
- RED: fail real provider usage charging and reservation release on failure, rejection and cancellation.
- GREEN: durably reserve, settle token + cost from provider usage, and release unused amounts exactly once.
- Acceptance: current organization/agent tests and typechecks; focused usage/cancel differential fixtures.
- Commit boundary: ledger math then lifecycle integration as separate O3 commits if needed.
- Rollback/blocker: fail closed without losing historical ledger; negative balance, double charge or stuck reservation blocks Jobs/release.
- Definition of complete: every terminal path has an auditable usage settlement and no leaked reservation.

### O4 — Real approval lifecycle

- Depends on: O1, O2.
- Allowed/forbidden scope: durable approval policy/request/decision/expiry; forbid automatic approval as production truth and UI work.
- RED: crash/retry duplicate decisions, stale revisions, expiry and cancellation around a real gated action.
- GREEN: make approval a tenant-scoped durable state machine with idempotent decision and action release.
- Acceptance: current organization/agent tests and typechecks; real-peer approval recovery test.
- Commit boundary: approval aggregate and execution seam are a single semantic package.
- Rollback/blocker: unresolved approval stays blocked; any unauthorized or duplicate action blocks O5+ and Jobs.
- Definition of complete: approval survives restart and releases exactly one authorized action or a durable denial/cancel.

### O5 — Artifact owner

- Depends on: O1, O2, O4.
- Allowed/forbidden scope: single durable artifact metadata/blob owner and tenant ACL; forbid attachment transport and tool-local side stores.
- RED: prove duplicate artifact owners, partial metadata/blob write, hash mismatch and cross-tenant reads.
- GREEN: establish atomic content-addressed artifact storage with durable event references and access checks.
- Acceptance: current organization/agent tests and typechecks; focused crash/hash/tenant tests.
- Commit boundary: artifact owner/storage contract is independent from C1 attachment ingestion.
- Rollback/blocker: preserve blobs and indexes; orphaned data, hash ambiguity or owner bypass blocks C1/M1.
- Definition of complete: every artifact has one verified durable owner and replayable reference.

### O6 — Memory retrieval

- Depends on: O1, O2, O5.
- Allowed/forbidden scope: durable memory records/index/retrieval and provenance; forbid UI and unscoped global caches.
- RED: fail tenant/project filters, deterministic ranking fixtures, restart rebuild and artifact provenance.
- GREEN: port mature memory semantics into durable events/indexes with bounded, attributable retrieval.
- Acceptance: current organization/agent tests and typechecks; v0.7.2 semantic comparison fixtures.
- Commit boundary: record/index then retrieval policy may be separate O6 commits.
- Rollback/blocker: rebuild from durable truth or retain old reader; cross-tenant or irreproducible retrieval blocks bundle/migration.
- Definition of complete: retrieval is scoped, replayable and traceable to durable source records.

### O7 — Evolution proposal/apply/rollback

- Depends on: O1, O4, O5, O6.
- Allowed/forbidden scope: proposal, review, artifacted patch, apply and rollback domain lifecycle; forbid self-modification outside approval/sandbox.
- RED: crash/reject/conflict around proposal approval, apply and rollback; prove unaudited mutation is possible today.
- GREEN: represent every evolution step as durable intent/result with approval, immutable artifact and reversible apply.
- Acceptance: current organization/agent tests and typechecks; real-repository apply/rollback fixture.
- Commit boundary: proposal/approval and apply/rollback are separately reviewable O7 commits.
- Rollback/blocker: restore prior tree and retain audit artifact; dirty/unrecoverable workspace or policy bypass blocks P2.
- Definition of complete: approved changes apply once, rejected changes never apply, and rollback restores the verified prior state.

### O8 — End-to-end cancellation and process tree

- Depends on: H5, O1, O2, O3, O4, O5, O6, O7.
- Allowed/forbidden scope: Host RPC, Organization, Agent, model, tool and child process cancellation/timeout; forbid UI polish and silent detach.
- RED: cancel/timeout at every boundary and expose durable-fault gaps, leaked budget/approval reservations and orphan descendants.
- GREEN: propagate one cancellation identity through the full tree, settle durable terminal events and kill/reap descendants.
- Acceptance: current host/agent/organization tests and typechecks; `to be introduced by O8: pnpm test:cancel-tree`.
- Commit boundary: propagation contract then each boundary adapter in narrow O8 commits.
- Rollback/blocker: retain durable cancel intent and fail loud; any running descendant or unsettled reservation blocks S1/J1/product entry.
- Definition of complete: cancel/timeout reaches every boundary, records one terminal fault and leaves zero live descendants.

### E1 — projects-official migration

- Depends on: O1, O2, E3; parallel with other ready enterprise plugins.
- Allowed/forbidden scope: `projects-official` durable project registry and differential tests; forbid shared generic Map factory and UI.
- RED: v0.7.2 project lifecycle, persistence, tenant isolation and authorization mismatch.
- GREEN: replace placeholder Map with a tenant-scoped durable project implementation using O2 truth.
- Acceptance: current `pnpm test:enterprise`; current `pnpm typecheck:enterprise`; project semantic differential fixtures.
- Commit boundary: only projects-official implementation/tests/migration adapter.
- Rollback/blocker: keep old production plugin; data loss or tenant/RBAC mismatch blocks P2 bundle.
- Definition of complete: project semantics persist and match authorized v0.7.2 behavior.

### E2 — rbac-official migration

- Depends on: O1, E3.
- Allowed/forbidden scope: `rbac-official` policy store/evaluator/audit seam; forbid allow-by-default and shared Map factory.
- RED: role inheritance, deny precedence, stale policy, cross-tenant and restart differential cases.
- GREEN: port durable tenant-scoped RBAC with revisioned decisions and audit references.
- Acceptance: current enterprise tests/typecheck; v0.7.2 RBAC differential and dual-tenant tests.
- Commit boundary: RBAC storage/evaluator is one security-focused commit.
- Rollback/blocker: fail closed and retain old rules; any authorization bypass blocks every remote/product surface.
- Definition of complete: every protected capability has deterministic, durable, tenant-scoped authorization.

### E3 — tenants-official migration

- Depends on: O1; security root for E1/E2/E4–E9.
- Allowed/forbidden scope: `tenants-official` identity/registry/isolation; forbid implicit default tenant and generic Map persistence.
- RED: create/update/delete/restart and two-tenant isolation differ from v0.7.2.
- GREEN: implement a durable tenant registry and require explicit tenant context at enterprise boundaries.
- Acceptance: current enterprise tests/typecheck; dual-tenant restart differential tests.
- Commit boundary: tenant registry and boundary enforcement only.
- Rollback/blocker: preserve old tenant data; missing scope or cross-tenant lookup blocks all enterprise/product work.
- Definition of complete: tenant identity survives restart and no enterprise record is accessible without explicit scope.

### E4 — mcp-oauth-official migration

- Depends on: E2, E3, E5, O4.
- Allowed/forbidden scope: `mcp-oauth-official` OAuth flow/token storage/refresh; forbid plaintext logs, credential bypass and shared Map factory.
- RED: callback state/PKCE, refresh rotation, revoke, restart, redaction and cross-tenant differential cases.
- GREEN: use durable encrypted credential ownership, exact grants, RBAC and audited refresh/revoke.
- Acceptance: current enterprise tests/typecheck; OAuth security/differential fixtures with redacted failures.
- Commit boundary: OAuth state machine/credential store is a security-isolated commit.
- Rollback/blocker: revoke/retain old token source safely; any token leak or scope bypass blocks MCP transport/release.
- Definition of complete: OAuth lifecycle is durable, tenant/RBAC scoped and secrets never enter events/logs.

### E5 — audit-official migration

- Depends on: O1, E3.
- Allowed/forbidden scope: `audit-official` append-only durable audit records/export; forbid mutable records and shared Map factory.
- RED: ordering/hash/restart/tenant/export parity and tamper detection versus v0.7.2.
- GREEN: create append-only scoped audit truth referenced by security/domain decisions.
- Acceptance: current enterprise tests/typecheck; audit integrity and differential fixtures.
- Commit boundary: audit storage/query contract only.
- Rollback/blocker: retain immutable old/new records; tamper ambiguity or missing security events blocks product/release.
- Definition of complete: relevant decisions produce ordered, durable, tenant-scoped, tamper-evident audit records.

### E6 — calendar-official migration

- Depends on: E2, E3, O2.
- Allowed/forbidden scope: `calendar-official` resources/reservations/conflicts; forbid generic Map and UI.
- RED: overlapping reservation, timezone, cancel, restart and tenant/RBAC differences from v0.7.2.
- GREEN: port durable resource-calendar rules with revisioned conflict detection.
- Acceptance: current enterprise tests/typecheck; calendar semantic differential fixtures.
- Commit boundary: calendar storage and scheduling policy only.
- Rollback/blocker: keep old schedule readable; double booking or scope bypass blocks bundle.
- Definition of complete: reservations are durable, authorized and conflict-equivalent to v0.7.2.

### E7 — dependency-graph-official migration

- Depends on: E1, E2, E3, O2.
- Allowed/forbidden scope: `dependency-graph-official` durable graph/query/validation; forbid generic Map and presentation UI.
- RED: cycle, deletion, restart, tenant and graph-query differential cases.
- GREEN: port a revisioned durable project/task dependency graph with cycle prevention.
- Acceptance: current enterprise tests/typecheck; graph semantic differential fixtures.
- Commit boundary: graph owner/validation/query only.
- Rollback/blocker: preserve old graph; cycle acceptance or lost edge blocks bundle/migration.
- Definition of complete: graph state replays exactly and invalid/cross-tenant edges are rejected.

### E8 — evolution-official migration

- Depends on: E2, E3, E5, O7.
- Allowed/forbidden scope: `evolution-official` policy/facade over O7; forbid second evolution runtime or generic Map.
- RED: proposal/approval/apply/rollback authorization and audit differ from v0.7.2.
- GREEN: adapt enterprise policy to the single O7 state machine with tenant/RBAC/audit enforcement.
- Acceptance: current enterprise tests/typecheck; evolution semantic differential and rollback fixtures.
- Commit boundary: enterprise adapter/policy only; O7 remains runtime owner.
- Rollback/blocker: disable adapter without altering O7 history; duplicate owner or unaudited apply blocks bundle.
- Definition of complete: enterprise evolution preserves old policy semantics without owning parallel state.

### E9 — ui-cards-official migration

- Depends on: E2, E3, S2.
- Allowed/forbidden scope: `ui-cards-official` keyed registration metadata; forbid remote settings ownership and direct React state mutation.
- RED: duplicate/dispose/revision/tenant/RBAC behavior and stale card replacement.
- GREEN: expose durable, keyed, authorized card descriptors backed by S2 settings namespaces.
- Acceptance: current enterprise tests/typecheck; keyed replacement/disposal tests.
- Commit boundary: UI card plugin contract only; rendering belongs S3.
- Rollback/blocker: hide the card while retaining settings; stale or cross-tenant descriptor blocks S3.
- Definition of complete: card registration is keyed, disposable, scoped and cannot bypass settings authority.

### S1 — OS/container sandbox

- Depends on: H5, O8; parallel with mature domain lanes.
- Allowed/forbidden scope: sandbox provider, Code Mode launch and security tests; forbid process-local permission theater and silent fallback.
- RED: escape symlink/TOCTOU/interpreter boundaries; violate default-deny network, read-only base, declared workspace mount and CPU/memory/process/output limits.
- GREEN: require a real OS/container provider for Code Mode and fail loud when unavailable.
- Acceptance: current agent tests/typecheck; `to be introduced by S1: pnpm test:sandbox-security`.
- Commit boundary: provider contract, platform adapters and each escape fix are separately reviewable S1 commits.
- Rollback/blocker: disable Code Mode rather than run unsandboxed; any escape or missing resource limit blocks Jobs/artifacts.
- Definition of complete: supported platforms pass adversarial isolation tests and unsupported environments fail closed.

### C1 — Atomic attachment ingestion

- Depends on: H3, H4A, H5, O5.
- Allowed/forbidden scope: typed attachment validation and O5 artifact writes; forbid transport/UI and side-channel artifact stores.
- RED: mixed invalid batch, oversize/type/hash/tenant errors and mid-write crash expose partial writes or untyped faults.
- GREEN: preflight the whole batch, then atomically persist ordered verified artifacts and durable references.
- Acceptance: current agent/organization tests/typechecks; `to be introduced by C1: pnpm test:attachments`.
- Commit boundary: attachment contract/atomic ingestion only.
- Rollback/blocker: commit zero artifacts on any preflight/write fault; residue or owner bypass blocks C2.
- Definition of complete: every batch is ordered and all-or-nothing with typed errors and one artifact owner.

### C2 — Tool rich-content projection

- Depends on: C1, H4B.
- Allowed/forbidden scope: generated tool-result content projection/replay; forbid Code nested-image execution and transports.
- RED: tool text/image/file results lose order, replay differently or bypass artifact references.
- GREEN: project tool outputs through generated content blocks and the same durable replay authority.
- Acceptance: current agent tests/typecheck; `to be introduced by C2: pnpm test:tool-content`.
- Commit boundary: tool projection and replay tests only.
- Rollback/blocker: fall back to verified durable content; dangling or reordered references block C3.
- Definition of complete: live and replayed tool content are ordered, typed and artifact-backed.

### C3 — Code nested-image content

- Depends on: C2, S1.
- Allowed/forbidden scope: Code tool nested-image extraction/limits under sandbox; forbid network transport and unsafe host paths.
- RED: nested/recursive image outputs evade limits, ordering, sandbox mounts or artifact verification.
- GREEN: flatten bounded nested content into generated blocks backed by C1 artifacts inside S1 policy.
- Acceptance: current agent tests/typecheck; `to be introduced by C3: pnpm test:code-rich-content`.
- Commit boundary: nested-image parsing/limits are one Code-specific commit.
- Rollback/blocker: reject the whole result with typed fault; sandbox escape or partial artifact blocks C4.
- Definition of complete: nested images remain bounded, ordered, verified and sandbox-contained.

### C4 — rich content/attachments/MCP/ACP adapters

- Depends on: C1, C2, C3, O8.
- Allowed/forbidden scope: outbound MCP/ACP adapter route/cancel/verified content transport; forbid inbound Host MCP ownership and bypass logs/artifacts.
- RED: route mismatch, cancel race, unverified attachment and content/replay divergence against real adapters.
- GREEN: adapt generated content and artifact references over verified MCP/ACP channels with O8 cancellation.
- Acceptance: current host/agent tests/typechecks; `to be introduced by C4: pnpm test:rich-content-transport`.
- Commit boundary: MCP and ACP adapters may be separate C4 commits, each sharing the same owner contracts.
- Rollback/blocker: disable the adapter and retain durable content; unverifiable transport or orphan request blocks T2/J2.
- Definition of complete: both adapters preserve typed order, verification, cancellation and durable ownership.

### J1 — Durable one-shot product Job

- Depends on: H4A, H4B, H5, O3, O4, O5, O8, S1.
- Allowed/forbidden scope: Host/Agent durable Job lifecycle/recovery/cancel/races; forbid upstream process-local ledger copies and preset auto-enable.
- RED: submit/retry/restart/cancel/approval/budget/tenant/process-cleanup races lose or duplicate terminal results.
- GREEN: create one durable Job aggregate using existing event, budget, approval, artifact and process owners.
- Acceptance: current host/agent/organization tests/typechecks; `to be introduced by J1: pnpm test:durable-jobs`.
- Commit boundary: lifecycle/store, execution integration and race fixes are narrow J1 commits.
- Rollback/blocker: leave job pending/failed durably and clean processes; duplicate work, cross-tenant access or leaked reservation blocks J2.
- Definition of complete: every accepted Job reaches exactly one durable terminal state and recovers safely.

### J2 — Codex and Claude Job adapters

- Depends on: J1, C4.
- Allowed/forbidden scope: provider adapters translating Job inputs/results/cancel; forbid second job ledger and provider-specific durable truth.
- RED: Codex/Claude success/failure/cancel/rich-content mappings diverge or duplicate retries.
- GREEN: implement thin adapters over J1 and generated content/fault contracts.
- Acceptance: current agent tests/typecheck; `to be introduced by J2: pnpm test:job-adapters`.
- Commit boundary: one adapter per independently reviewable J2 commit.
- Rollback/blocker: disable failing adapter while retaining J1 state; provider side effects without durable result block J3.
- Definition of complete: both providers preserve one Job lifecycle, typed content and cancellation semantics.

### J3 — Explicit preset Job opt-in

- Depends on: J2.
- Allowed/forbidden scope: presets capability declarations/config/tests; forbid default enablement and runtime duplication.
- RED: a preset gains Job behavior without explicit opt-in or lacks required providers.
- GREEN: add explicit, validated Job opt-in referencing J1/J2 capabilities.
- Acceptance: current `pnpm test:presets`; current `pnpm typecheck:presets`; Job-enabled/disabled preset tests.
- Commit boundary: preset manifest/config changes only.
- Rollback/blocker: remove opt-in without touching durable jobs; implicit activation or missing capability blocks profiles.
- Definition of complete: Jobs run only in explicitly opted-in, dependency-valid presets.

## P2 — Product entry points and user-visible profiles

### P1 — Workbench / Web Server / Headless / Python SDK profiles

- Depends on: H5, O1, O2, O3, O4, O5, O6, O7, O8, S1, J3.
- Allowed/forbidden scope: Host profile composition, SDK boundary and profile tests; forbid transport/UI implementation and legacy deletion.
- RED: each profile fails owner/dependency/lifecycle matrix or accidentally exposes unavailable capabilities.
- GREEN: compose four explicit profiles over the same Host/Organization/Agent owners with fail-closed dependencies.
- Acceptance: current host/agent/organization/presets tests and typechecks; `to be introduced by P1: pnpm test:product-profiles`.
- Commit boundary: profile manifests/composition and contract tests only.
- Rollback/blocker: keep alpha entrypoints fail-closed; duplicate owners or inconsistent lifecycle blocks P2/T1/D1.
- Definition of complete: all four profiles start/stop deterministically and expose only declared capabilities.

### P2 — software-company bundle

- Depends on: P1, O2, O3, O4, O5, O6, O7, E1, E2, E3, E4, E5, E6, E7, E8, E9, J3.
- Allowed/forbidden scope: bundle composition/config/scenario tests; forbid reimplementing Organization or enterprise behavior.
- RED: real-repository delivery cannot exercise task, budget, approval, artifact, memory, evolution and enterprise capabilities together.
- GREEN: compose one `software-company` bundle from reviewed owners and explicit profile requirements.
- Acceptance: current organization/presets/enterprise tests/typechecks; `to be introduced by P2: pnpm test:software-company-e2e`.
- Commit boundary: bundle manifest/wiring and its real-repository scenario only.
- Rollback/blocker: disable the bundle without altering components; fake-only delivery or missing domain evidence blocks migration/release.
- Definition of complete: the bundle delivers a real repository change with durable recovery and every named domain capability.

### T1 — Host HTTP/WebSocket transport

- Depends on: H5, O8, P1.
- Allowed/forbidden scope: Host transport auth/tenant/generated RPC/fault/backpressure/resume cursor; forbid React stores, MCP facade and Python runtime ownership.
- RED: real socket auth/tenant isolation, reconnect/cursor, slow consumer, cancel and typed fault tests fail.
- GREEN: expose generated RPC over HTTP and ordered resumable events over WebSocket with bounded backpressure.
- Acceptance: current host tests/typecheck; `to be introduced by T1: pnpm test:host-transport`.
- Commit boundary: HTTP RPC and WebSocket stream may be separate T1 commits under one transport owner.
- Rollback/blocker: close connections with typed faults; auth leak, skipped cursor or unbounded queue blocks UI/settings/Electron.
- Definition of complete: authenticated scoped clients resume without loss/duplication and cannot bypass Host ownership.

### T2 — Host MCP transport

- Depends on: T1, C4.
- Allowed/forbidden scope: inbound Host MCP transport translating generated Host capabilities; forbid another runtime/store owner and Codex-specific facade.
- RED: MCP handshake/auth/tenant/content/cancel/backpressure behavior diverges from T1/Host contracts.
- GREEN: add an adapter over existing Host owners, generated faults and C4 content semantics.
- Acceptance: current host/agent tests/typechecks; `to be introduced by T2: pnpm test:host-mcp-transport`.
- Commit boundary: generic Host MCP adapter only.
- Rollback/blocker: disable MCP listener while preserving Host; any side database or authorization bypass blocks T3.
- Definition of complete: MCP is a verified transport adapter with no independent runtime truth.

### T3 — Codex MCP facade

- Depends on: T2, J2.
- Allowed/forbidden scope: Codex-facing MCP naming/config/translation; forbid runtime ownership, duplicate Job state and generic MCP changes.
- RED: Codex tool/resource calls, cancel and rich results fail facade-to-T2 conformance.
- GREEN: implement a thin Codex MCP facade over T2/J2 contracts.
- Acceptance: current host/agent tests/typechecks; `to be introduced by T3: pnpm test:codex-mcp-facade`.
- Commit boundary: facade/config/tests only.
- Rollback/blocker: remove facade without data migration; any provider-specific truth or auth bypass blocks release.
- Definition of complete: Codex clients use the same Host lifecycle, Jobs, content and tenant policy.

### K1 — Seven CLI commands

- Depends on: P1, P2, T1, J3.
- Allowed/forbidden scope: CLI shared profile loader and `run / web / plugin / config / session / doctor / migrate`; forbid duplicate bootstrap and claiming prototype migrate as full M1.
- RED: current fail-closed run/web/config/session and inconsistent profile/config/error behavior fail command-level tests.
- GREEN: wire all seven commands to the shared profile loader; keep `migrate` safe but release-blocked until M1 completes.
- Acceptance: current `pnpm test:cli`; current `pnpm typecheck:cli`; process-level command tests.
- Commit boundary: loader first, then independently reviewable command families under K1.
- Rollback/blocker: commands fail closed with no partial state; any command bypassing profile/auth/tenant owners blocks product entry.
- Definition of complete: all seven commands perform real documented actions through one profile loader; M1 remains a separate release gate.

### U1 — React generated client/event projections

- Depends on: T1, O2, O3, O4, O5, O6, O7, E1, E2, E3, E4, E5, E6, E7, E8, E9.
- Allowed/forbidden scope: generated client, event projections and one store/page at a time; forbid old DTO deletion before real Host E2E.
- RED: each legacy store/page fails generated RPC/event typing, reconnect/cursor/auth/tenant behavior against a real Host.
- GREEN: migrate one page/store per commit to generated contracts and deterministic projections.
- Acceptance: in `frontend`, current `npm test -- --run`, current `npm run build`, current `npm run i18n:check`, current `npm run e2e:real`; the current real E2E is regression evidence until T1-backed scenarios are added.
- Commit boundary: each store/page migration is independently reviewable and reversible.
- Rollback/blocker: retain old path behind an explicit boundary; any generated/legacy mixed owner or missing real Host E2E blocks L1.
- Definition of complete: every production page uses generated RPC/events and passes real Host reconnect/auth/tenant flows.

### D1 — Electron Host→Python supervision

- Depends on: P1, T1, O8.
- Allowed/forbidden scope: Electron launches/supervises Cordis Host; Host alone supervises Python; forbid Electron→Python direct ownership and installer claims.
- RED: start, Host/Python crash recovery, shutdown and descendant cleanup fail source-tree Electron integration.
- GREEN: implement the single Electron→Host→Python supervision chain with durable recovery signals.
- Acceptance: current host/organization tests/typechecks; `to be introduced by D1: pnpm test:electron-supervision`.
- Commit boundary: launch protocol and lifecycle/recovery fixes are narrow D1 commits.
- Rollback/blocker: preserve legacy launcher until A1; dual supervisors or orphan child blocks packaging/deletion.
- Definition of complete: Electron controls only Host and every exit/crash reaps the complete descendant tree.

### S2 — Plugin settings ownership and remote surface

- Depends on: T1, E2, E3, E5; Host-local namespace phase must GREEN before remote phase.
- Allowed/forbidden scope: Host-local keyed namespace/register/dispose/invalidate, then explicit remote exposure with tenant/RBAC/redaction/revision CAS/audit; forbid registration implying visibility/writeability.
- RED: duplicate/stale registration, disposer replacement, secret leakage, cross-tenant read/write, stale revision and missing audit cases.
- GREEN: establish Host-local owner first; only then add an allowlisted transport surface with redaction, CAS and audit.
- Acceptance: current host/enterprise tests/typechecks; `to be introduced by S2: pnpm test:plugin-settings`.
- Commit boundary: local ownership and remote exposure are separate, ordered S2 commits.
- Rollback/blocker: disable remote exposure while retaining local settings; secret leak, stale overwrite or authorization bypass blocks S3/release.
- Definition of complete: settings have one keyed local owner and remote access is explicit, scoped, redacted, revisioned and audited.

### S3 — Keyed settings UI card

- Depends on: S2, U1, E9.
- Allowed/forbidden scope: React keyed card discovery/render/edit through generated settings client; forbid direct plugin state or secret rendering.
- RED: duplicate replacement/disposal, stale revision, permission-denied and redacted secret UI tests fail.
- GREEN: render keyed cards from authorized descriptors and submit revision-CAS mutations through S2.
- Acceptance: current frontend tests/build/i18n/real E2E; `to be introduced by S3: npm run e2e:settings` (from `frontend`).
- Commit boundary: card registry projection and each UI surface are narrow S3 commits.
- Rollback/blocker: hide card without deleting settings; stale overwrite or secret exposure blocks L1/release.
- Definition of complete: authorized users see current keyed cards, conflicts are explicit and secrets never round-trip to UI.

## P3 — Migration, proof, deletion and release

### M1 — lossless full data tree migration

- Depends on: H5, O2, O3, O4, O5, O6, O7, E1, E2, E3, E4, E5, E6, E7, E8, E9, P2, K1.
- Allowed/forbidden scope: migration of session/task/checkpoint/ledger/budget/approval/changes/memory/artifact/fork/governance; forbid deleting old readers or destructive in-place conversion.
- RED: complete v0.7.2 fixtures fail manifest/hash, projection comparison, idempotency and injected mid-tree rollback.
- GREEN: inventory and hash the full tree, make whole-directory backup, convert atomically, compare projections and rollback the entire batch on failure.
- Acceptance: current `pnpm test:cli`; current `uv run pytest -q`; `to be introduced by M1: pnpm test:migration-full-tree` and `to be introduced by M1: pnpm test:migration-installed-rollback`.
- Commit boundary: inventory/manifest, converters and rollback/projection gates are separate M1 commits.
- Rollback/blocker: source plus whole-directory backup remain immutable; any missing node/hash mismatch/partial batch blocks L5/A1/R1.
- Definition of complete: every listed data class round-trips without loss and injected failure restores the exact pre-migration tree.

### B1 — three benchmark runners/golden/CI

- Depends on: P2, T3, M1.
- Allowed/forbidden scope: three existing scenario classes, runner, immutable evidence, thresholds/update policy and CI; forbid YAML-only completion and silent golden refresh.
- RED: `greenfield-repository`, `existing-repository-repair` and `software-company-lifecycle` each lack an executable result or fail their explicit threshold before implementation.
- GREEN: add deterministic runners, reviewed golden evidence and CI comparisons with an approval rule for updates.
- Acceptance: `to be introduced by B1: uv run python benchmarks/v1/run.py --all --check-golden`; `to be introduced by B1: uv run pytest -q tests/benchmarks`.
- Commit boundary: one runner/golden pair per benchmark, then one CI gate commit.
- Rollback/blocker: retain prior goldens and failure artifacts; regression, nondeterminism or unreviewed refresh blocks R1.
- Definition of complete: all three runners meet thresholds in CI and retain attributable result artifacts.

### A1 — Windows/macOS/Linux installed artifacts

- Depends on: D1, P1, P2, T1, K1, M1.
- Allowed/forbidden scope: packaging owner/installers and installed smoke on three OSes; forbid source-tree Node/Python substitution and legacy-backend ownership.
- RED: packaged app without system Node/Python fails start, Standard/both Code modes, recovery, migration, cancel and process cleanup on each OS.
- GREEN: bundle Node Host/Python plane and required runtimes/assets under one installer lifecycle per platform.
- Acceptance: `to be introduced by A1: pnpm test:installed:windows`; `to be introduced by A1: pnpm test:installed:macos`; `to be introduced by A1: pnpm test:installed:linux`.
- Commit boundary: packaging owner first, then one independently reviewable platform artifact/gate per A1 commit.
- Rollback/blocker: keep last alpha artifact and do not publish broken platform; system-runtime dependency or orphan process blocks G1/L4/R1.
- Definition of complete: all three installed artifacts pass the full smoke without system Node/Python.

### G1 — optional dependency/release graph/notices/SBOM

- Depends on: A1 and all runtime/package owners it inventories.
- Allowed/forbidden scope: module-scope optional-import gate, packed missing-dependency tests, release graph, license/notices freshness and SBOM; forbid source-tree-only substitution.
- RED: remove each optional dependency from a real packed artifact and expose eager import; make graph/notices/SBOM stale against the payload.
- GREEN: isolate optional imports, generate one release dependency graph and derive verified notices/SBOM from actual artifacts and pinned provenance.
- Acceptance: `to be introduced by G1: pnpm test:packed-missing-deps`; `to be introduced by G1: uv run python scripts/check_release_graph.py`; `to be introduced by G1: uv run python scripts/check_notices_sbom.py`.
- Commit boundary: optional import fixes, graph generation and notices/SBOM gates are separate G1 commits.
- Rollback/blocker: retain prior artifacts/notices and block publication; undeclared payload, missing license or source-only proof blocks deletion/release.
- Definition of complete: packed artifacts tolerate absent optional modules and graph/notices/SBOM exactly match shipped payloads.

### L1 — Delete legacy server/transport/DTO/React paths

- Depends on: T1, T2, T3, U1, S3 and their real Host E2E evidence.
- Allowed/forbidden scope: old server REST/SSE/WS, duplicate DTOs and migrated React stores/pages only; forbid unrelated legacy/domain deletion.
- RED: a focused import/route test still reaches each named old path after production entrypoints switch.
- GREEN: delete only covered modules and enable no-old-server/DTO/store checks for their consumers.
- Acceptance: current frontend tests/build/i18n/real E2E plus `to be introduced by L1: pnpm test:zero-old-product-surface`.
- Commit boundary: one server/transport or page/store family per deletion commit, each citing replacement tests.
- Rollback/blocker: revert the deletion commit alone; any missing real transport reconnect/cursor/auth/tenant evidence blocks deletion.
- Definition of complete: production product surfaces use generated Host transport/client and no deleted path is imported.

### L2 — Delete SessionDriver/AgentRuntime/domain legacy paths

- Depends on: P2, O1, O2, O3, O4, O5, O6, O7, O8, C1, C2, C3, C4, J1, J2, J3, S1 and real repository/recovery evidence.
- Allowed/forbidden scope: covered SessionDriver, old AgentRuntime, workflow/meetings/ledger/budget/memory/evolution modules; forbid enterprise/data-reader deletion.
- RED: production imports or semantic scenarios still depend on each target module.
- GREEN: delete one covered module family and tighten import/behavior gates to reviewed replacements.
- Acceptance: current Python and all v1 package regressions; `to be introduced by L2: pnpm test:zero-old-domain-runtime`.
- Commit boundary: every module family deletion is an independent commit referencing its replacement tests.
- Rollback/blocker: revert the single deletion if behavior regresses; fake-only Software Company evidence blocks all deletion.
- Definition of complete: real repository delivery/recovery uses only durable dual-plane owners for every listed domain.

### L3 — Delete legacy enterprise implementations

- Depends on: E1, E2, E3, E4, E5, E6, E7, E8, E9 and their persistence/permission/two-tenant differential evidence.
- Allowed/forbidden scope: corresponding old project/tenant/RBAC/OAuth/audit/calendar/dependency/evolution code only; forbid broad factory cleanup before all consumers migrate.
- RED: production import or v0.7.2 semantic fixture still selects each old implementation.
- GREEN: remove one enterprise implementation at a time and require its reviewed v1 plugin.
- Acceptance: current enterprise tests/typecheck; `to be introduced by L3: pnpm test:zero-old-enterprise`.
- Commit boundary: exactly one enterprise plugin family per deletion commit.
- Rollback/blocker: revert only that family; any persistence, authorization or tenant-isolation gap blocks deletion.
- Definition of complete: every enterprise capability is backed solely by its durable v1 plugin and no shared Map placeholder remains.

### L4 — Delete Electron legacy backend launch path

- Depends on: D1, A1 and three-platform installed process cleanup evidence.
- Allowed/forbidden scope: Electron legacy backend bootstrap/supervision only; forbid Host/Python or unrelated UI deletion.
- RED: installed artifact startup still invokes or can fall back to the old Python backend owner.
- GREEN: remove the old launcher and enforce the sole Electron→Host→Python chain.
- Acceptance: all A1 installed commands; `to be introduced by L4: pnpm test:zero-electron-legacy-backend`.
- Commit boundary: legacy Electron launch deletion is one reversible commit.
- Rollback/blocker: revert deletion, not supervision data; any platform lacking installed smoke blocks this package.
- Definition of complete: every installed platform starts only Host, and Host alone owns Python lifecycle.

### L5 — Delete old data readers

- Depends on: M1 and installed migration/rollback evidence from A1.
- Allowed/forbidden scope: old session/checkpoint/memory/artifact/fork/governance readers only; forbid source deletion or backup removal.
- RED: migrated installed artifacts still access an old reader or fail projection/hash comparison without it.
- GREEN: remove readers one data family at a time after the full-tree converter owns that format.
- Acceptance: all M1 commands and installed rollback; `to be introduced by L5: pnpm test:zero-old-data-readers`.
- Commit boundary: one data-family reader deletion per independent commit.
- Rollback/blocker: restore reader while keeping immutable source/backup; any unconverted fixture blocks deletion.
- Definition of complete: full-tree migration is the only old-format ingress and runtime never reads legacy data directly.

### L6 — Delete old CLI/EventBus/log/types/docs and enable zero-legacy gate

- Depends on: L1, L2, L3, L4, L5, K1, H3.
- Allowed/forbidden scope: remaining old CLI, EventBus/log, duplicated event/DTO/types and invalidated active docs; preserve historical handoff/provenance.
- RED: repository-wide gate enumerates every remaining production legacy import, old vocabulary and duplicate DTO/event type.
- GREEN: delete each final covered target, then enable the mandatory zero-legacy import/DTO/event/runtime gate.
- Acceptance: all current base/product regressions; `to be introduced by L6: pnpm test:zero-legacy`.
- Commit boundary: deletion families are separate commits; the final zero-legacy gate is its own commit.
- Rollback/blocker: revert the failing deletion while retaining historical evidence; any production match blocks R1.
- Definition of complete: zero-legacy gate is GREEN and no production entrypoint depends on old runtime, DTO or event vocabulary.

### R1 — Version 1.0.0, tag and release

- Depends on: H1, H2, H3, H4A, H4B, H5, O1, O2, O3, O4, O5, O6, O7, O8, E1, E2, E3, E4, E5, E6, E7, E8, E9, S1, C1, C2, C3, C4, J1, J2, J3, P1, P2, T1, T2, T3, K1, U1, D1, S2, S3, M1, B1, A1, G1, L1, L2, L3, L4, L5, L6.
- Allowed/forbidden scope: version/metadata/tag/release/assets and immutable evidence only; forbid feature fixes, skipped gates and alpha removal before dependencies pass.
- RED: release verification detects non-1.0.0 versions, tag/artifact/provenance mismatch or any missing gate/evidence.
- GREEN: synchronize every product version to `1.0.0`, generate final metadata/assets, create signed/annotated tag and publish only the reviewed immutable artifacts.
- Acceptance: all current/future gates in this roadmap; `to be introduced by R1: uv run python scripts/verify_v1_release.py`; `to be introduced by R1: pnpm release:v1 --verify`.
- Commit boundary: version/metadata synchronization is one reviewed commit; tag and release occur only after that exact HEAD passes final review.
- Rollback/blocker: do not move/publish the tag; withdraw an unpublished candidate and retain audit/rollback evidence. Any unchecked dependency blocks release.
- Definition of complete: immutable `1.0.0` tag/release/assets match provenance and every P0–P3 gate is GREEN with no open Critical/Important.

## Command registry and evidence level

Current base gates (real commands, not proof of product completion):

```powershell
uv run pytest -q
pnpm typecheck:host
pnpm typecheck:agent
pnpm typecheck:organization
pnpm typecheck:presets
pnpm typecheck:enterprise
pnpm typecheck:cli
pnpm test:host
pnpm test:agent
pnpm test:organization
pnpm test:presets
pnpm test:enterprise
pnpm test:cli
uv run python scripts/generate_protocol.py --check
uv run python scripts/verify_agent_notes.py
git diff --check
```

Current product regression gates, run from `frontend` and then return to repository root:

```powershell
Set-Location frontend
npm test -- --run
npm run build
npm run i18n:check
npm run e2e:real
Set-Location ..
```

These are current legacy/front-end regression evidence; `npm run e2e:real` is not yet the T1-backed installed-product
gate. Every nonexistent future gate is written at its owning package as `to be introduced by <ID>` and must first
fail there. H5/C1/C2/C3/C4/J1/J2/O8/S1/P1/P2/T1/T2/T3/D1/S2/S3/M1/B1/A1/G1/L1–L6/R1 own those commands;
none is claimed GREEN today.

## Legacy deletion prerequisite matrix

Each deletion target is a separate commit, cites replacement tests, receives independent review and can be rolled
back alone. No row may start while its replacement evidence is still being created.

| Legacy deletion target | Replacement evidence required first | Owner |
|---|---|---|
| Old server, REST/SSE/WS, DTO, React stores/pages | Generated client for every page plus real Host transport E2E, reconnect/cursor/auth/tenant | L1 after T1–T3/U1/S3 |
| SessionDriver, AgentRuntime, workflow/meetings/ledger/budget/memory/evolution | Software Company real-repository delivery plus recovery, approval, usage budget, artifact, memory/evolution | L2 after O1–O8/C/J/P2/S1 |
| RBAC, tenancy, OAuth MCP, audit, calendar, dependency graph, evolution enterprise implementations | Per-plugin semantics, durable storage, permission and two-tenant differential evidence | L3 after E1–E9 |
| Electron legacy backend startup | Electron→Host→Python three-platform installed smoke and process cleanup | L4 after D1/A1 |
| Old session/checkpoint/memory/artifact/fork/governance readers | Full-tree manifest/hash/projection comparison and whole-batch rollback in installed artifact | L5 after M1/A1 |
| Old CLI, EventBus/log, duplicate events/DTO/types and invalid active docs | All production entries switched and zero-import/zero-old-vocabulary gate | L6 after L1–L5/K1/H3 |

All deletion packages and the L6 zero-legacy gate must be GREEN before any `1.0.0` synchronization or release tag.

## Definition of Done

- Every stable ID in P0–P3 has an implementation commit, genuine RED/GREEN evidence and independent reviewer dual
  PASS; no Critical/Important finding remains.
- Durable event, artifact and runtime ownership is singular. Four product profiles, seven CLI commands, React,
  Electron and MCP facade share the generated protocol and dual-plane lifecycle.
- Organization, enterprise plugins, sandbox, attachments and Jobs are real implementations with real E2E,
  security and recovery evidence—not placeholders or shared Maps.
- Full-tree migration/rollback, three benchmark runners/golden/CI, three-platform installed artifacts without
  system Node/Python, packed optional-dependency checks and license/notices/SBOM are GREEN.
- Legacy is deleted only through the prerequisite matrix; L6's zero-legacy gate is GREEN while historical evidence
  and provenance documents remain intact.
- All versions are `1.0.0`; the reviewed tag, release and assets match immutable provenance and preserve rollback
  and audit evidence.

## Next action

下一步立即执行：H1 / Task 16.11. Resume the existing RED intent without rewriting or staging it as Task 16.9b.
After H1 is GREEN, committed and reviewed, automatically execute `H1 → H2 → H3`: enter Task 16.11a and then
Task 16.12 不询问是否继续. Pause only for a real blocker, new authority requirement or review finding.
