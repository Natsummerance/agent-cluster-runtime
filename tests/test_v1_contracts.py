from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "protocol" / "schema" / "doai-v1.schema.json"


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

    assert provenance["upstream"]["commit"] == "47f943859bef60e4160492346772ded9b24f765a"
    assert provenance["upstream"]["license"] == "MIT"
    assert provenance["policy"]["automatic_tracking"] is False


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
