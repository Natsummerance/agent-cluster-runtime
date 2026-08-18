# DeepSeek Harness rc.7 baseline and adoption order

Status: Implemented

## Evidence

The fixed upstream range is
`47f943859bef60e4160492346772ded9b24f765a..99f6f02fecdb7dff40c3fbc9470f5907c29f74ca`:
111 commits, 539 files, 8,183 insertions, and 1,625 deletions. The head is tag
`dsh-v0.1.0-rc.7`. Cordis remains `4.0.1`, and `vendor/cordis` has no path changes in the range.
The LICENSE is unchanged MIT; the head blob SHA-256 is
`EBB4F09972AEE8608BE255DEBAF78451A68E95C290F55C240DEC2ECFA16EA6BE`.

Evidence came from the local read-only upstream with `git rev-parse`, `git tag --points-at`,
`git rev-list --count`, `git diff --shortstat`, path diffs, and commit-local source/test inspection. Upstream
tests were treated as authored contract evidence, not reported as fresh local passes.

## Decision

- ADR-0004 moves the active differential reference from rc.5 to rc.7. ADR-0003 remains the historical
  baseline/import evidence; ADR-0001 dual-plane ownership and ADR-0002 single event truth remain in force.
- Provenance records current and previous baselines, immutable license URL/hash, exact delta statistics, and
  retains every existing import's real rc.5 source commit. `automatic_tracking: false` remains mandatory.
- Task 16.11 continues unchanged except for one transaction regression: primary activation/start/health
  failure plus rollback failure must both survive in sanitized structured form, cleanup must settle, and old
  active/Host/plugin/provider dependency epochs must not advance. The model-visible/tool-schema snapshot is a
  separate test-only commit after 16.11.
- Later adoption follows typed vocabulary → durable store/replay → real crash matrix → durable attachments →
  durable product Jobs → authorized settings → installed-artifact/release gates. No runtime capability was
  ported by this documentation task.

## Explicit divergence

- Keep the product name `Code Mode`; do not follow the `PTC mode` UI rename.
- Wait for a generated cross-provider capability catalog before adding DeepSeek `low` reasoning effort.
- Use a stable structural corruption code/discriminant, never cross-package `error.name`.
- Do not apply persistent Bash behavior to current one-shot `process.run`.
- Reuse settings registry ownership/keyed cards only with explicit remote authorization, tenant/RBAC, secret
  redaction, and revision CAS; registration is not remote write permission.
- Product subagents must be durable, recoverable, and tenant/budget/approval controlled; a process-local Job
  ledger is not acceptable.

## TDD and verification

RED:

- `uv run pytest tests/test_v1_contracts.py -q` — `1 failed, 7 passed`; provenance still returned
  `47f943859bef60e4160492346772ded9b24f765a` instead of the new active head.

GREEN:

- `uv run pytest tests/test_v1_contracts.py -q` — `8 passed in 0.48s`.
- `uv run python scripts/verify_agent_notes.py` and `git diff --check` are final acceptance gates for the task.

## Remaining boundary

rc.7 does not add Windows Python single-exe: Python SDK single-exe remains Linux/macOS, and the new resolver is
Linux node-pty only. Windows native CI/node-pty beta coverage cannot satisfy DoAI's three-platform dual-runtime
installed-artifact gate. MCP transactional `notifications/tools/list_changed` also predates rc.7; the relevant
MCP delta is durable image result projection. Attachments, replay, MCP/ACP, settings, background Jobs, PTY,
frontend, legacy removal, and packaging remain unimplemented until their ordered tasks begin.
