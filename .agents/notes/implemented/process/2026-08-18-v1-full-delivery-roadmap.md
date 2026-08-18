# DoAI v1 full delivery master roadmap

Status: Implemented

## Problem

The current handoff accurately records prototype evidence and the rc.7 sync plan orders the upstream delta, but
neither is a single executable plan for every remaining package from Task 16.11 through the v1.0.0 release. That
gap could let work skip P0 durability, count placeholder or untracked tests as completion, invent release commands,
or delete legacy before replacement evidence exists.

## Decision

- Make `docs/superpowers/plans/2026-08-18-doai-v1-full-delivery-roadmap.md` the sole active execution source.
  Keep the handoff as current-state/evidence and release-readiness as the release/deletion gate; retain the rc.7
  plan as specialist delta detail.
- Freeze the non-parallel P0 spine as H1 activation → H2 test-only snapshot → H3 generated vocabulary → H4A
  durable store → H4B replay same-mask → H5 real crash matrix. The existing Task 16.11 RED is explicitly
  untracked, uncommitted and incomplete.
- Give every P0–P3 package a stable ID, dependencies, allowed/forbidden scope, observable RED, minimal GREEN,
  acceptance commands, commit boundary, rollback/blocker and objective completion definition.
- Distinguish currently executable regression commands from future product/release gates. Every future command
  is owned by an explicit `to be introduced by <ID>` package.
- Require a fixed implementer/read-only reviewer loop, review before push, and at most five Critical/Important
  fix rounds. Require per-module replacement evidence before legacy deletion and make version/tag/release last.

## TDD evidence

RED:

- `uv run pytest -q tests/test_v1_contracts.py -k "master_roadmap"` — `3 failed, 9 deselected`; the new master
  path did not exist, so coverage, next-action and active-index contracts failed.

GREEN:

- `uv run pytest -q tests/test_v1_contracts.py -k "master_roadmap"` — `3 passed, 9 deselected`.
- `uv run pytest -q tests/test_v1_contracts.py` — `12 passed`.
- `uv run pytest -q` — `897 passed, 4 skipped`; warnings are existing ldap3/pyasn1 deprecations and test-key
  length warnings, with no failures.
- `uv run python scripts/verify_agent_notes.py` — `agent notes tree OK: .agents\\notes`.
- `git diff --check` — exit 0 (line-ending notices only; no whitespace errors).

## Scope and follow-up

This task changes documentation, focused documentation contracts and this Agent Note only. It does not change
runtime, frontend, legacy code or the untracked activation-policy RED. The next action is H1 / Task 16.11; after
reviewed GREEN it automatically advances to H2 / Task 16.11a and H3 / Task 16.12 without asking to continue.

## Review fix

The first independent review found that the master omitted real streaming-model proof, Creator supply-chain and
installed lifecycle work, and the final five-mode E2E gate. It also found that the original G1 incorrectly mixed
an early optional-dependency gate with final release metadata, and that H2 described retaining a failing snapshot
instead of making the test-only contract GREEN.

The fix adds N1 (real model streaming), X1 (Creator trust/conformance), Q1 (Standard/Code-Python/
Code-TypeScript/Minimal/Creator real E2E), and X2 (Creator installed install/upgrade/revoke/rollback). G1 now owns
only the pre-optional-package import/real-packed missing-dependency gate; G2 runs after X2 and L1–L6 to derive the
release graph/notices/SBOM from the frozen final payload. H2 commits only a passing snapshot contract; a runtime
defect blocks H2 and requires separately authorized repair. H4A is present in the future-command owner summary.

Review-fix RED:

- `uv run pytest -q tests/test_v1_contracts.py -k "master_roadmap"` — `2 failed, 1 passed, 9 deselected`;
  the five new stable IDs were absent and H2 lacked the passing-contract/blocked-runtime semantics.

Review-fix GREEN:

- `uv run pytest -q tests/test_v1_contracts.py -k "master_roadmap"` — `3 passed, 9 deselected`.
- `uv run pytest -q tests/test_v1_contracts.py` — `12 passed`.
- Final acceptance also runs the Agent Note verifier and whitespace/range checks; the ignored Task 16.9b report
  retains their exact output and the activation RED hash.
