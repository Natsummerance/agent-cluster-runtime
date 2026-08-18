from __future__ import annotations

import json
import re
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
MASTER_ROADMAP = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-18-doai-v1-full-delivery-roadmap.md"
)
MASTER_ROADMAP_IDS = {
    "H1", "H2", "H3", "H4A", "H4B", "H5",
    "O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8",
    "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9",
    "S1", "C1", "C2", "C3", "C4", "J1", "J2", "J3",
    "P1", "P2", "T1", "T2", "T3", "K1", "U1", "D1", "S2", "S3",
    "M1", "B1", "A1", "G1", "L1", "L2", "L3", "L4", "L5", "L6", "R1",
}
MASTER_ROADMAP_FIELDS = {
    "Depends on:",
    "Allowed/forbidden scope:",
    "RED:",
    "GREEN:",
    "Acceptance:",
    "Commit boundary:",
    "Rollback/blocker:",
    "Definition of complete:",
}


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


def test_dsh_rc7_plan_defers_replay_alignment_until_after_durable_store() -> None:
    plan = (
        ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-08-18-dsh-rc7-sync-implementation.md"
    ).read_text(encoding="utf-8")
    task_16_12 = plan.split("## 3. Task 16.12", 1)[1].split("## 4. Task 16.13", 1)[0]
    task_16_13 = plan.split("## 4. Task 16.13", 1)[1].split("## 5. Task 16.14", 1)[0]

    assert "replay envelope 是生成类型" in task_16_12
    assert "durable content 永远权威" in task_16_12
    assert "keep/drop mask" not in task_16_12
    assert "以同一 mask 处理 content/replay" in task_16_13
    assert "A 不绿不得开始 B" in task_16_13


def _master_roadmap_packages(text: str) -> dict[str, str]:
    headings = list(re.finditer(r"^### ([A-Z][A-Z0-9]*) — .+$", text, re.MULTILINE))
    packages: dict[str, str] = {}
    for index, heading in enumerate(headings):
        package_id = heading.group(1)
        assert package_id not in packages, f"duplicate roadmap package ID: {package_id}"
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        packages[package_id] = text[heading.start():end]
    return packages


def test_v1_master_roadmap_has_executable_packages_for_all_four_phases() -> None:
    assert MASTER_ROADMAP.exists(), "Task 16.9b master roadmap has not been created"
    text = MASTER_ROADMAP.read_text(encoding="utf-8")
    packages = _master_roadmap_packages(text)

    assert MASTER_ROADMAP_IDS == set(packages)
    for package_id, package in packages.items():
        missing = MASTER_ROADMAP_FIELDS - set(
            re.findall(r"^- ([A-Za-z/ ]+:)", package, re.MULTILINE)
        )
        assert not missing, f"{package_id} is missing execution fields: {sorted(missing)}"

    for phase in ("## P0", "## P1", "## P2", "## P3"):
        assert phase in text
    for required_contract in (
        "Organization durable state machine",
        "token + cost",
        "end-to-end cancellation",
        "OS/container sandbox",
        "rich content/attachments/MCP/ACP",
        "durable product Jobs",
        "Workbench / Web Server / Headless / Python SDK",
        "software-company",
        "Host HTTP/WebSocket/MCP transport",
        "run / web / plugin / config / session / doctor / migrate",
        "React generated client/event projections",
        "Electron Host→Python supervision",
        "plugin settings",
        "Codex MCP facade",
        "lossless full data tree migration",
        "three benchmark runners/golden/CI",
        "Windows/macOS/Linux installed artifacts",
        "optional dependency/release graph/notices/SBOM",
    ):
        assert required_contract in text


def test_v1_master_roadmap_preserves_order_next_action_and_release_gates() -> None:
    text = MASTER_ROADMAP.read_text(encoding="utf-8")
    packages = _master_roadmap_packages(text)

    assert "Task 16.11 RED：已存在、未跟踪、未提交、未完成" in text
    assert "activation-policy.test.ts" in text
    assert "不得作为完成证据" in text
    assert "1.0.0-alpha.0` prototype" in text
    assert "v0.7.2 remains the production path" in text
    assert "release and legacy deletion are blocked" in text
    assert "none is claimed GREEN today" in text
    assert "下一步立即执行：H1 / Task 16.11" in text
    assert "H1 → H2 → H3" in text
    assert "不询问是否继续" in text
    assert [text.index(f"### {item} —") for item in ("H1", "H2", "H3", "H4A", "H4B", "H5")] == sorted(
        text.index(f"### {item} —") for item in ("H1", "H2", "H3", "H4A", "H4B", "H5")
    )
    assert "H4A" in packages["H4B"] and "H4A, H4B" in packages["H5"]
    assert "to be introduced by H5" in packages["H5"]
    assert "to be introduced by R1" in packages["R1"]

    assert "fixed implementer" in text
    assert "read-only reviewer" in text
    assert "review before push" in text
    assert "Critical/Important" in text and "最多 5 轮" in text
    assert "## Legacy deletion prerequisite matrix" in text
    assert "## Definition of Done" in text
    assert "zero-legacy" in text
    assert "1.0.0" in packages["R1"]


def test_v1_master_roadmap_is_the_active_execution_index() -> None:
    roadmap_name = MASTER_ROADMAP.name
    handoff = (
        ROOT
        / "docs"
        / "superpowers"
        / "handoff"
        / "2026-08-17-v1-cordis-continuation.md"
    ).read_text(encoding="utf-8")
    readiness = (ROOT / "docs" / "v1" / "release-readiness.md").read_text(
        encoding="utf-8"
    )
    master = MASTER_ROADMAP.read_text(encoding="utf-8")

    for index in (handoff, readiness):
        assert roadmap_name in index
        assert "活动执行源" in index
    assert "2026-08-18-dsh-rc7-sync-implementation.md" in master
    assert "rc.7 专项细节" in master
    assert "handoff 是现状与证据" in master
    assert "release-readiness 是发布与删除门禁" in master


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
