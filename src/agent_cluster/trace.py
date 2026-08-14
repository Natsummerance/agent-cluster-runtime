"""可观测性基础（v0.5 T12.5）：span 追踪 + 审计导出 + 健康指标。

- ``Span``：一次可观测区间（模型调用 / 工具 / 节点 / 门），含耗时与 token。
- ``Tracer``：线程安全 span 记录（父子嵌套），支持导出器。
- ``Exporter``：导出接口（协议）；``JsonlExporter`` 写 ``.agent-cluster/trace.jsonl``；
  ``OTelExporter`` 为预留占位（未接入时 no-op，避免引入依赖）。
- ``build_audit_package``：把事件/审批/账本/变更/span 打包为 Markdown + JSON 审计包，
  支持按格式（csv/json/markdown）追加导出轨迹文件。
- ``hash_record``/``build_hash_chain``/``verify_hash_chain``：审计记录 sha256 哈希链防篡改。
- ``apply_retention``：按 retention_days 裁剪审计轨迹（保留策略）。
- ``export_audit``：导出轨迹为 CSV/JSON/Markdown，导出前校验哈希链。
- ``compute_health``：四类健康指标（eval 通过率趋势 / token 成本 / 预估准确率 / 返工率）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "Span",
    "Tracer",
    "Exporter",
    "JsonlExporter",
    "OTelExporter",
    "build_audit_package",
    "apply_retention",
    "build_hash_chain",
    "export_audit",
    "hash_record",
    "verify_hash_chain",
    "AuditChainError",
    "compute_health",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Span:
    """可观测区间。"""

    id: str
    name: str
    kind: str = "span"
    parent_id: str = ""
    start_ts: float = field(default_factory=time.time)
    end_ts: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        end = self.end_ts if self.end_ts is not None else time.time()
        return max(0.0, (end - self.start_ts) * 1000)

    def finish(self) -> None:
        self.end_ts = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "parent_id": self.parent_id,
            "duration_ms": round(self.duration_ms, 3),
            "start": _now_iso(),
            "meta": self.meta,
        }


class Exporter(Protocol):
    """span 导出器接口（OTel/Langfuse 等后续接入点）。"""

    def export(self, span: Span) -> None: ...


class JsonlExporter:
    """把 span 追加写入 JSONL 文件（可 git 追踪/审计）。"""

    def __init__(self, workspace: str | Path) -> None:
        self.path = Path(workspace).expanduser().resolve() / ".agent-cluster" / "trace.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, span: Span) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(span.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            return


class OTelExporter:
    """OTLP 导出占位（v0.5 预留接口）：未配置时不输出，避免引入依赖。"""

    def __init__(self, endpoint: str = "") -> None:
        self.endpoint = endpoint

    def export(self, span: Span) -> None:
        # 预留：接入 OpenTelemetry 时在此把 span 转换为 OTLP 并上报
        return


class Tracer:
    """线程安全 span 追踪器（每线程维护栈，支持父子嵌套）。"""

    def __init__(self, exporter: Exporter | None = None) -> None:
        self.exporter = exporter
        self._lock = threading.Lock()
        self._spans: dict[str, Span] = {}
        self._local = threading.local()

    def start_span(self, name: str, kind: str = "span", **meta: Any) -> Span:
        parent_id = getattr(self._local, "stack", [])[-1] if getattr(self._local, "stack", []) else ""
        span = Span(id=uuid.uuid4().hex[:12], name=name, kind=kind, parent_id=parent_id, meta=meta)
        with self._lock:
            self._spans[span.id] = span
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = self._local.stack = []
        stack.append(span.id)
        return span

    def end_span(self, span: Span | None) -> None:
        if span is None:
            return
        span.finish()
        stack = getattr(self._local, "stack", [])
        if span.id in stack:
            stack.remove(span.id)
        with self._lock:
            if self.exporter is not None:
                try:
                    self.exporter.export(span)
                except Exception:  # noqa: BLE001 —— 导出失败不阻断运行
                    pass

    def spans(self) -> list[Span]:
        with self._lock:
            return list(self._spans.values())


# ---------------------------------------------------------------------------
# 审计导出
# ---------------------------------------------------------------------------


def _attr(obj: Any, name: str, default: Any = "") -> Any:
    """兼容 dict 与对象的事件字段读取。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def build_audit_package(
    *,
    workspace: str | Path,
    session_id: str,
    goal: str,
    events: list[Any],
    approvals: list[Any],
    token_summary: dict[str, Any],
    change_records: list[Any],
    spans: list[Span],
    cost: dict[str, Any] | None = None,
    export_format: str = "",
    retention_days: int | None = None,
) -> dict[str, str]:
    """打包审计文件（Markdown + JSON）到 ``.agent-cluster/audit/``，返回文件路径。

    ``export_format``（csv|json|markdown）时另写轨迹导出文件（含哈希链），
    ``retention_days`` 控制保留窗口；返回键含 ``export``。
    """
    root = Path(workspace).expanduser().resolve()
    audit_dir = root / ".agent-cluster" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stamp = f"{session_id[:8]}-{ts}"

    events_data = [
        {
            "type": _attr(event, "type", "event"),
            "actor": _attr(event, "actor", ""),
            "ts": _attr(event, "ts", "").isoformat()
            if hasattr(_attr(event, "ts", ""), "isoformat")
            else str(_attr(event, "ts", "")),
            "payload": _attr(event, "payload", {}),
        }
        for event in events
    ]
    approvals_data = [
        {
            "request_id": _attr(approval, "request_id", ""),
            "decision": _attr(approval, "decision", ""),
            "ts": str(_attr(approval, "ts", "")),
            "reason": _attr(approval, "reason", ""),
        }
        for approval in approvals
    ]
    changes_data = [record.model_dump() if hasattr(record, "model_dump") else dict(record) for record in change_records]

    audit = {
        "session_id": session_id,
        "goal": goal,
        "generated_at": _now_iso(),
        "events": events_data,
        "approvals": approvals_data,
        "token_summary": token_summary,
        "cost": cost or {},
        "changes": changes_data,
        "spans": [span.to_dict() for span in spans],
    }

    json_path = audit_dir / f"audit-{stamp}.json"
    json_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        f"# 审计报告：{session_id}",
        "",
        f"- 目标：{goal}",
        f"- 生成时间：{audit['generated_at']}",
        f"- 事件：{len(events_data)} 条｜审批：{len(approvals_data)} 条｜变更：{len(changes_data)} 条",
        "",
        "## Token 计量",
    ]
    for key, value in (token_summary or {}).items():
        md_lines.append(f"- {key}: {value}")
    if cost:
        md_lines.append("")
        md_lines.append("## 成本")
        md_lines.append(f"- 总金额: {cost.get('total', 0):.4f} {cost.get('currency', 'USD')}")
        for model, amount in (cost.get("by_model") or {}).items():
            md_lines.append(f"- {model}: {amount:.4f}")
    md_lines.append("")
    md_lines.append("## 需求变更历史")
    for change in changes_data:
        md_lines.append(f"- v{change.get('version')} [{change.get('phase')}] {change.get('text')}")
    md_lines.append("")
    md_lines.append("## 审批记录")
    for approval in approvals_data:
        md_lines.append(f"- {approval.get('request_id')}: {approval.get('decision')}（{approval.get('reason', '')}）")
    md_path = audit_dir / f"audit-{stamp}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    result: dict[str, str] = {"markdown": str(md_path), "json": str(json_path), "stamp": stamp}
    if export_format:
        ext = "md" if export_format == "markdown" else export_format
        content = export_audit(
            events,
            fmt=export_format,
            retention_days=retention_days,
            session_id=session_id,
            goal=goal,
        )
        trajectory_path = audit_dir / f"audit-{stamp}.trajectory.{ext}"
        trajectory_path.write_text(content, encoding="utf-8")
        result["export"] = str(trajectory_path)
    return result


# ---------------------------------------------------------------------------
# 审计哈希链与合规导出（v0.7 T14.11）
# ---------------------------------------------------------------------------


class AuditChainError(ValueError):
    """审计哈希链校验失败（拒绝导出被篡改的轨迹）。"""


def hash_record(record: dict[str, Any], prev_hash: str = "") -> str:
    """对单条记录做 sha256 摘要（排除 hash 字段自身；prev_hash 参与摘要）。

    摘要输入为规范化 JSON（键排序、紧凑分隔符），保证跨运行确定性。
    """
    content = {key: value for key, value in record.items() if key != "hash"}
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{canonical}|{prev_hash}".encode("utf-8")).hexdigest()


def build_hash_chain(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为记录列表附加 ``prev_hash``/``hash``，构成防篡改链（首条 prev_hash 为空）。"""
    chained: list[dict[str, Any]] = []
    prev = ""
    for record in records:
        item = dict(record)
        item["prev_hash"] = prev
        item["hash"] = hash_record(item, prev)
        prev = item["hash"]
        chained.append(item)
    return chained


def verify_hash_chain(records: list[dict[str, Any]]) -> bool:
    """校验哈希链：结构完整 + 逐条摘要一致 + 前后链接闭合。空链视为合法。"""
    prev = ""
    for record in records:
        if not isinstance(record, dict):
            return False
        if record.get("prev_hash", "") != prev:
            return False
        if record.get("hash", "") != hash_record(record, prev):
            return False
        prev = record["hash"]
    return True


def _parse_ts_seconds(ts: Any) -> float | None:
    """把事件 ts 解析为 epoch 秒；无法解析返回 None（保留策略视为不裁剪）。"""
    if isinstance(ts, (int, float)):
        return float(ts)
    text = str(ts or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def apply_retention(records: list[dict[str, Any]], retention_days: int | None) -> list[dict[str, Any]]:
    """保留策略：仅保留 retention_days 天内的记录（None/<=0 表示全保留）。"""
    if not retention_days or retention_days <= 0:
        return list(records)
    cutoff = time.time() - retention_days * 86400
    kept: list[dict[str, Any]] = []
    for record in records:
        seconds = _parse_ts_seconds(_attr(record, "ts", ""))
        if seconds is None or seconds >= cutoff:
            kept.append(record)
    return kept


def _payload_summary(record: dict[str, Any], limit: int = 120) -> str:
    payload = _attr(record, "payload", {})
    try:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(payload)
    return text[:limit]


def export_audit(
    records: list[dict[str, Any]],
    *,
    fmt: str = "json",
    retention_days: int | None = None,
    session_id: str = "",
    goal: str = "",
) -> str:
    """导出审计轨迹（csv|json|markdown），每条记录含 ``prev_hash``/``hash``。

    输入若已是哈希链（全部记录含 hash）则先校验，篡改即抛 ``AuditChainError``；
    保留策略在重新成链前裁剪。
    """
    fmt = (fmt or "json").strip().lower()
    if fmt not in ("csv", "json", "markdown"):
        raise ValueError(f"不支持的导出格式 format={fmt}（仅支持 csv|json|markdown）")
    if records and all(isinstance(record, dict) and "hash" in record for record in records):
        if not verify_hash_chain(records):
            raise AuditChainError("审计哈希链校验失败，拒绝导出")
    chained = build_hash_chain(apply_retention(records, retention_days))
    count = len(chained)
    generated_at = _now_iso()

    if fmt == "json":
        return json.dumps(
            {
                "session_id": session_id,
                "goal": goal,
                "generated_at": generated_at,
                "format": fmt,
                "verified": True,
                "retention_days": retention_days,
                "count": count,
                "records": chained,
            },
            ensure_ascii=False,
            indent=2,
        )

    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["seq", "ts", "type", "actor", "prev_hash", "hash", "payload"])
        for index, record in enumerate(chained, start=1):
            writer.writerow(
                [
                    _attr(record, "seq", index),
                    _attr(record, "ts", ""),
                    _attr(record, "type", "event"),
                    _attr(record, "actor", ""),
                    record.get("prev_hash", ""),
                    record.get("hash", ""),
                    _payload_summary(record),
                ]
            )
        return buffer.getvalue()

    lines = [
        f"# 审计轨迹导出：{session_id or '-'}",
        "",
        f"- 目标：{goal or '-'}",
        f"- 生成时间：{generated_at}",
        f"- 格式：markdown｜记录：{count}｜校验：通过（sha256）",
        f"- 保留策略：{retention_days} 天" if retention_days else "- 保留策略：全部",
        "",
        "| # | 时间 | 类型 | 行为者 | 摘要 | 哈希 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, record in enumerate(chained, start=1):
        lines.append(
            "| {} | {} | {} | {} | {} | {} |".format(
                _attr(record, "seq", index),
                _attr(record, "ts", ""),
                _attr(record, "type", "event"),
                _attr(record, "actor", ""),
                _payload_summary(record).replace("|", "\\|"),
                (record.get("hash", "") or "")[:12],
            )
        )
    lines.append("")
    lines.append(f"- 链首 hash：{chained[0]['hash'] if chained else ''}")
    lines.append(f"- 链尾 hash：{chained[-1]['hash'] if chained else ''}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 健康指标
# ---------------------------------------------------------------------------


def compute_health(
    *,
    token_ledger: Any,
    gate_decisions: list[Any],
    cost: dict[str, Any] | None = None,
    eval_history: list[dict] | None = None,
) -> dict[str, Any]:
    """四类健康指标：eval 通过率趋势 / token 成本 / 预估准确率 / 返工率。"""
    budget = int(getattr(token_ledger, "budget", 0) or 0)
    used = int(getattr(token_ledger, "total", lambda: 0)() if callable(getattr(token_ledger, "total", None)) else (getattr(token_ledger, "total", 0) or 0))
    accuracy = None
    try:
        accuracy = token_ledger.estimate_accuracy()
    except Exception:  # noqa: BLE001
        accuracy = None

    attempts = 0
    rejections = 0
    for record in gate_decisions or []:
        attempts += int(getattr(record, "attempts", 0) or 0)
        rejections += int(getattr(record, "rejections", 0) or 0)
    rework_rate = (rejections / attempts) if attempts else 0.0

    eval_trend = None
    if eval_history:
        eval_trend = {
            "latest": eval_history[-1].get("pass_rate"),
            "history": [round(item.get("pass_rate", 0.0), 4) for item in eval_history],
        }

    return {
        "eval_pass_rate_trend": eval_trend,
        "token_cost": {"used": used, "budget": budget, "cost": (cost or {}).get("total", 0.0), "currency": (cost or {}).get("currency", "USD")},
        "estimate_accuracy": round(accuracy, 4) if accuracy is not None else None,
        "rework_rate": round(rework_rate, 4),
    }
