from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "protocol" / "schema" / "doai-v1.schema.json"
CURRENT_DSH_COMMIT = "99f6f02fecdb7dff40c3fbc9470f5907c29f74ca"
CURRENT_DSH_RELEASE = "0.1.0-rc.7"
CURRENT_DSH_TAG = f"dsh-v{CURRENT_DSH_RELEASE}"
PREVIOUS_DSH_COMMIT = "47f943859bef60e4160492346772ded9b24f765a"
DSH_LICENSE_SHA256 = "EBB4F09972AEE8608BE255DEBAF78451A68E95C290F55C240DEC2ECFA16EA6BE"


def test_protocol_schema_declares_the_durable_event_contract() -> None:
    document = json.loads(SCHEMA.read_text(encoding="utf-8"))
    event = document["$defs"]["SessionEvent"]

    assert document["$id"] == "https://doai.dev/schema/protocol/v1"
    assert event["additionalProperties"] is False
    assert set(event["required"]) == {
        "schema_version",
        "session_id",
        "seq",
        "type",
        "ts",
        "scope",
        "payload",
        "ignorable",
    }
    assert event["properties"]["seq"]["minimum"] == 1


def test_mutation_request_requires_replay_guards() -> None:
    document = json.loads(SCHEMA.read_text(encoding="utf-8"))
    mutation = document["$defs"]["MutationMeta"]

    assert set(mutation["required"]) == {
        "request_id",
        "idempotency_key",
        "session_revision",
    }
    assert mutation["properties"]["session_revision"]["minimum"] == 0


def test_capability_catalog_has_unique_names_and_single_owners() -> None:
    catalog = yaml.safe_load(
        (ROOT / "docs" / "v1" / "capabilities.yaml").read_text(encoding="utf-8")
    )
    names = [item["name"] for item in catalog["capabilities"]]

    assert len(names) == len(set(names))
    assert all(item["owner"] in {"host", "organization", "plugin"} for item in catalog["capabilities"])
    assert all(item["provider_policy"] in {"exactly_one", "many", "optional"} for item in catalog["capabilities"])


def test_event_vocabulary_makes_model_content_replayable() -> None:
    vocabulary = yaml.safe_load(
        (ROOT / "docs" / "v1" / "events.yaml").read_text(encoding="utf-8")
    )
    events = vocabulary["events"]
    names = [item["type"] for item in events]

    assert len(names) == len(set(names))
    assert all(item["durable"] for item in events if item.get("model_visible"))
    assert {"model.requested", "model.completed", "tool.requested", "tool.completed"} <= set(names)


def test_state_machines_have_one_initial_and_terminal_states() -> None:
    document = yaml.safe_load(
        (ROOT / "docs" / "v1" / "state-machines.yaml").read_text(encoding="utf-8")
    )

    for machine in document["machines"]:
        assert machine["initial"] in machine["states"]
        assert set(machine["terminal"]) <= set(machine["states"])
        assert machine["terminal"]


def test_dsh_provenance_is_pinned_and_licensed() -> None:
    provenance = yaml.safe_load(
        (ROOT / "docs" / "porting" / "dsh-provenance.yaml").read_text(encoding="utf-8")
    )

    assert provenance["upstream"]["commit"] == CURRENT_DSH_COMMIT
    assert provenance["upstream"]["release"] == CURRENT_DSH_RELEASE
    assert provenance["upstream"]["license"] == "MIT"
    assert provenance["upstream"]["license_url"] == (
        f"https://github.com/deepseek-ai/deepseek-harness/blob/{CURRENT_DSH_COMMIT}/LICENSE"
    )
    assert provenance["upstream"]["license_sha256"] == DSH_LICENSE_SHA256
    assert provenance["previous_baseline"] == {
        "commit": PREVIOUS_DSH_COMMIT,
        "release": "0.1.0-rc.5",
    }
    assert provenance["delta"] == {
        "range": f"{PREVIOUS_DSH_COMMIT}..{CURRENT_DSH_COMMIT}",
        "commits": 111,
        "files_changed": 539,
        "insertions": 8183,
        "deletions": 1625,
    }
    assert provenance["policy"]["automatic_tracking"] is False
    assert provenance["policy"]["permitted_actions"] == [
        "reuse", "port", "adapt", "differential-test"
    ]
    assert provenance["policy"]["required_fields"] == [
        "source", "commit", "license", "action", "deviation", "verification"
    ]
    assert provenance["cordis"]["version"] == "4.0.1"
    assert provenance["cordis"]["commit"] == CURRENT_DSH_COMMIT
    assert provenance["cordis"]["source"] == "vendor/cordis"
    imports = {item["source"]: item for item in provenance["imports"]}
    assert set(imports) == {
        "vendor/cordis/src/events.ts",
        "vendor/cordis/src/fiber.ts",
        "vendor/cordis/src/context.ts",
    }
    assert {item["commit"] for item in imports.values()} == {PREVIOUS_DSH_COMMIT}
    assert all(
        all(item[field] for field in ("license", "action", "deviation", "verification"))
        for item in imports.values()
    )

    host_package = json.loads(
        (ROOT / "packages" / "host" / "package.json").read_text(encoding="utf-8")
    )
    assert host_package["dependencies"]["@deepseek-ai/cordis"] == provenance["cordis"]["version"]

    active_documents = [
        ROOT / "docs" / "adr" / "0004-upstream-baseline-rc7.md",
        ROOT / "docs" / "superpowers" / "specs" / "2026-08-17-v1-cordis-dual-plane-design.md",
        ROOT / "docs" / "v1" / "release-readiness.md",
        ROOT / "docs" / "superpowers" / "handoff" / "2026-08-17-v1-cordis-continuation.md",
        ROOT / "THIRD_PARTY_NOTICES.md",
    ]
    for path in active_documents:
        text = path.read_text(encoding="utf-8")
        assert CURRENT_DSH_COMMIT in text, path
        assert CURRENT_DSH_TAG in text, path

    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "MIT" in notices
    assert DSH_LICENSE_SHA256 in notices


def test_generated_protocol_types_are_fresh() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/generate_protocol.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_python_contract_rejects_unknown_fields() -> None:
    from doai_protocol import EventScope, SessionEvent

    event = SessionEvent(
        schema_version="1.0",
        session_id="s-1",
        seq=1,
        type="model.completed",
        ts="2026-08-17T00:00:00Z",
        scope=EventScope(tenant_id="t-1", project_id="p-1"),
        payload={"text": "ok"},
        ignorable=False,
    )
    assert event.seq == 1

    try:
        SessionEvent(**{**event.model_dump(), "unexpected": True})
    except ValueError:
        pass
    else:
        raise AssertionError("generated Pydantic model accepted an unknown field")

    try:
        SessionEvent(**{**event.model_dump(), "seq": 0})
    except ValueError:
        pass
    else:
        raise AssertionError("generated Pydantic model ignored the schema minimum")
