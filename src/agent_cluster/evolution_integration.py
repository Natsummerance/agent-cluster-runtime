"""进化集成模块（v0.5 T12.8）：记忆库失败模式 → 自动提案 → 复盘报告 → 纪要提炼/SOP 建议。

把两条系统打通：
- 记忆库（memory.py，SQLite 四级晋升 + 提议制写入）——失败模式/踩坑/复盘根因的沉淀池；
- 进化闭环（evolution.py，collect→distill→propose→review→apply→rollback 六步引擎）——
  带强制回滚方案、风险等级、自我扩权校验与审计事件。

EvolutionBridge 提供的四类能力（对应计划 T12.8）：
① ``generate_from_memory``：扫描记忆库候选/失败模式，自动生成进化提案
  （复用 ``EvolutionEngine.propose``，强制 rollback_plan + 自我扩权校验），
  提案持久化到 ``<root>/.agent-cluster/evolution/proposals.json``；
② ``generate_retro_report``：自动复盘报告（门返工 + token 计量 + 记忆失败模式），
  落盘 ``<root>/docs/retro-<ts>.md``；
③ ``capture_session_learnings``：会议纪要提炼入记忆（add_candidate + add_evidence），
  关键词扫描产出 SOP 建议候选并建晋升提案；
④ ``apply_proposal`` / ``rollback_proposal``：评审门 → 生效/回滚；
  process/organization 类提案生效后自动同步 ``.agent-cluster/SOP.md`` 变更记录。

安全约束：生成提案时若命中进化引擎的自我扩权校验（EvolutionError），
跳过该条目并在结果中记录 ``skipped``（不静默吞掉原因）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_cluster.evolution import Candidate, EvolutionEngine, EvolutionError, EvolutionProposal
from agent_cluster.memory import MemoryStore, MemoryStatus, Tier

__all__ = [
    "EvolutionBridge",
    "SOP_FILENAME",
    "DEFAULT_ROLLBACK_PLAN",
    "LEARNING_KINDS",
    "SOP_KEYWORDS",
]

# 建议类记忆的元数据 kind（满足任一即视为可进化的学习素材）
LEARNING_KINDS: tuple[str, ...] = ("failure", "gotcha", "lesson", "sop_suggestion", "retro")

# SOP 建议关键词（纪要/笔记中命中即提取为 SOP 建议候选）
SOP_KEYWORDS: tuple[str, ...] = (
    "SOP",
    "流程建议",
    "流程改进",
    "改进建议",
    "建议",
    "应该",
    "需要",
    "必须",
    "禁止",
)

SOP_FILENAME = "SOP.md"

DEFAULT_ROLLBACK_PLAN = "回滚：撤销该进化提案（恢复上一版本 SOP/流程配置并保留审计记录）"
DEFAULT_VALIDATION_PLAN = "灰度 1 个迭代验证后再全量"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _title_from_text(text: str, max_len: int = 60) -> str:
    """从文本提取标题：首个非空行，截断到 max_len。"""
    for line in text.splitlines():
        stripped = line.strip().strip("#").strip()
        if stripped:
            return stripped[:max_len]
    return text[:max_len]


class EvolutionBridge:
    """进化集成桥：记忆库 ↔ 进化引擎 ↔ 复盘报告 ↔ SOP 变更记录。"""

    def __init__(
        self,
        root: str | Path,
        *,
        memory_store: MemoryStore | None = None,
        engine: EvolutionEngine | None = None,
        proposals_path: str | Path | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.memory = memory_store or MemoryStore(self.root)
        self.engine = engine or EvolutionEngine()
        evo_dir = self.root / ".agent-cluster" / "evolution"
        evo_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_path = Path(proposals_path) if proposals_path else evo_dir / "proposals.json"
        self._proposals: dict[str, dict] = self._load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.proposals_path.read_text(encoding="utf-8"))
            return {item["id"]: item for item in data if isinstance(item, dict) and item.get("id")}
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return {}

    def _save(self) -> None:
        payload = list(self._proposals.values())
        tmp = self.proposals_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self.proposals_path)

    @property
    def sop_path(self) -> Path:
        """SOP 变更记录文件路径。"""
        return self.root / ".agent-cluster" / SOP_FILENAME

    # ------------------------------------------------------------------
    # ① 记忆 → 自动提案
    # ------------------------------------------------------------------

    def generate_from_memory(
        self,
        *,
        min_evidence: int = 2,
        limit: int = 20,
        author_role: str = "evolution",
    ) -> dict[str, Any]:
        """扫描记忆库生成进化提案。

        入选条件（任一）：meta.kind 命中 LEARNING_KINDS，或
        evidence_count >= min_evidence 且状态为 active（跨会话反复命中的经验）。
        已生成过提案的记忆条目（change.memory_id 已存在）跳过。
        返回 ``{"created": [...], "skipped": [...]}``。
        """
        created: list[dict] = []
        skipped: list[dict] = []
        proposed_memory_ids = {
            proposal.get("change_diff", {}).get("memory_id")
            for proposal in self._proposals.values()
            if isinstance(proposal.get("change_diff"), dict)
        }
        for item in self.memory.list_items(limit=500):
            if len(created) >= limit:
                break
            if item.status == MemoryStatus.ARCHIVED:
                continue
            if item.id in proposed_memory_ids:
                continue
            meta = item.meta or {}
            kind = str(meta.get("kind", ""))
            eligible = kind in LEARNING_KINDS or (
                item.evidence_count >= min_evidence and item.status == MemoryStatus.ACTIVE
            )
            if not eligible:
                continue
            content = item.content(self.root) or item.title
            category = self._category_for_item(item, kind)
            candidate = Candidate(
                category=category,
                target=item.title,
                change={
                    "kind": "improve",
                    "target": item.title,
                    "memory_id": item.id,
                    "note": content[:500],
                },
                evidence=[
                    f"memory:{item.id}:{item.tier}",
                    f"source={item.source or 'memory'}",
                    f"evidence_count={item.evidence_count}",
                    f"category={category}",
                    f"target={item.title}",
                ],
                expected_impact=f"修复「{item.title}」暴露的失败模式（记忆证据 {item.evidence_count} 次）",
            )
            try:
                proposal = self.engine.propose(
                    candidate,
                    author_role=item.source or author_role,
                    title=f"[记忆] {item.title}",
                    rollback_plan=DEFAULT_ROLLBACK_PLAN,
                    validation_plan=DEFAULT_VALIDATION_PLAN,
                )
            except EvolutionError as exc:
                skipped.append({"title": item.title, "reason": str(exc)})
                continue
            dump = proposal.model_dump(mode="json")
            self._proposals[proposal.id] = dump
            created.append(dump)
        self._save()
        return {"created": created, "skipped": skipped}

    @staticmethod
    def _category_for_item(item: Any, kind: str) -> str:
        """记忆条目 → 进化对象类别：meta.category 优先，否则按来源/层级推导。"""
        meta = item.meta or {}
        tagged = meta.get("category")
        if tagged in ("skill", "knowledge", "process", "organization"):
            return tagged
        if kind == "sop_suggestion":
            return "process"
        if kind in ("gotcha", "retro"):
            return "knowledge"
        if item.tier == Tier.GOTCHA.value:
            return "knowledge"
        if item.source in ("qa", "test", "dev", "review"):
            return "skill"
        return "process"

    # ------------------------------------------------------------------
    # ② 自动复盘报告
    # ------------------------------------------------------------------

    def generate_retro_report(
        self,
        *,
        goal: str = "",
        session_id: str = "",
        token_summary: dict[str, Any] | None = None,
        gate_decisions: list[Any] | None = None,
        events: list[Any] | None = None,
        title: str = "自动复盘报告",
    ) -> Path:
        """生成复盘 Markdown：门返工 + token 计量 + 记忆失败模式 + 根因/建议。

        落盘 ``<root>/docs/retro-<ts>.md`` 并返回路径。
        """
        docs_dir = self.root / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = docs_dir / f"retro-{ts}.md"

        lines: list[str] = []
        lines.append(f"# {title}\n")
        lines.append(f"- 生成时间：{_now_iso()}")
        if session_id:
            lines.append(f"- 会话：`{session_id}`")
        if goal:
            lines.append(f"- 目标：{goal}")
        lines.append("")

        # ① 门决策与返工
        lines.append("## 一、返工与门决策")
        if gate_decisions:
            lines.append("| 门 | 类别 | 尝试 | 返工 | 升级 |")
            lines.append("|---|---|---|---|---|")
            for item in gate_decisions:
                data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
                node = data.get("node", "")
                kind = data.get("kind", "")
                attempts = int(data.get("attempts", 0))
                rej = int(data.get("rejections", 0))
                esc = bool(data.get("escalated", False))
                lines.append(f"| {node} | {kind} | {attempts} | {rej} | {'是' if esc else '-'} |")
        else:
            lines.append("（本次无门决策记录）")
        lines.append("")

        # ② token 计量
        lines.append("## 二、Token 计量")
        if token_summary:
            summary = token_summary if isinstance(token_summary, dict) else {}
            budget = summary.get("budget", 0)
            used = summary.get("used", 0)
            lines.append(f"- 预算：{budget} tokens")
            lines.append(f"- 消耗：{used} tokens（剩余 {summary.get('remaining', budget - used)}）")
            acc = summary.get("estimate_accuracy")
            lines.append(f"- 预估准确率：{f'{acc:.1%}' if acc is not None else '无真实调用数据'}")
            by_phase = summary.get("by_phase") or {}
            if by_phase:
                lines.append("\n按阶段消耗：")
                for phase, tokens in sorted(by_phase.items(), key=lambda pair: pair[1], reverse=True):
                    lines.append(f"- `{phase}`：{tokens} tokens")
            by_role = summary.get("by_role") or {}
            if by_role:
                lines.append("\n按角色消耗：")
                for role, tokens in sorted(by_role.items(), key=lambda pair: pair[1], reverse=True):
                    lines.append(f"- `{role}`：{tokens} tokens")
        else:
            lines.append("（无 token 计量数据）")
        lines.append("")

        # ③ 记忆失败模式
        lines.append("## 三、记忆失败模式")
        failures = self._memory_failure_items(limit=10)
        if failures:
            for item in failures:
                lines.append(f"- **{item.title}**（{item.tier} / 证据 {item.evidence_count}）")
        else:
            lines.append("（当前无失败模式记忆）")
        lines.append("")

        # ④ 根因与改进建议
        lines.append("## 四、根因与改进建议")
        root_causes = self._collect_root_causes(events)
        if root_causes:
            lines.append("已识别的根因：")
            for cause in root_causes:
                lines.append(f"- {cause}")
        else:
            lines.append("（未收集到显式根因）")
        lines.append("")
        lines.append("改进建议：")
        lines.append("- 返工率高的门：补充验收标准与检查清单，必要时提前召开设计评审。")
        lines.append("- token 消耗高的阶段：考虑拆分任务或启用前缀缓存（deepseek/Anthropic）。")
        lines.append("- 重复出现的失败模式：提交 `agent-cluster evolution generate` 生成进化提案。")
        lines.append("")
        lines.append("---")
        lines.append("> 本报告由 `agent-cluster evolution retro` 自动生成，仅作复盘参考。")

        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path

    def _memory_failure_items(self, limit: int = 10) -> list[Any]:
        items = self.memory.list_items(limit=500)
        ranked: list[tuple[int, Any]] = []
        for item in items:
            if item.status == MemoryStatus.ARCHIVED:
                continue
            meta = item.meta or {}
            kind = str(meta.get("kind", ""))
            if kind in LEARNING_KINDS or item.source in ("qa", "test", "review"):
                ranked.append((item.evidence_count, item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in ranked[:limit]]

    @staticmethod
    def _collect_root_causes(events: list[Any] | None) -> list[str]:
        if not events:
            return []
        causes: list[str] = []
        for event in events:
            payload = event.payload if hasattr(event, "payload") else event.get("payload", {})
            event_type = event.type if hasattr(event, "type") else event.get("type", "")
            if not isinstance(payload, dict):
                payload = {}
            if event_type == "retro" and payload.get("root_cause"):
                root = payload["root_cause"]
                roots = root if isinstance(root, list) else [root]
                for cause in roots:
                    if str(cause).strip() and str(cause) not in causes:
                        causes.append(str(cause))
            elif event_type == "review_result" and str(payload.get("verdict", "")).lower() in (
                "reject",
                "rejected",
                "lbtm",
            ):
                target = payload.get("target") or ""
                if target:
                    causes.append(f"评审驳回：{target}")
        return causes[:10]

    # ------------------------------------------------------------------
    # ③ 会议纪要提炼 + SOP 建议
    # ------------------------------------------------------------------

    def capture_session_learnings(
        self,
        *,
        meeting_notes: str | list[str] | list[dict] | None = None,
        events: list[Any] | None = None,
        session_id: str = "",
        source: str = "auto",
        sop_keywords: tuple[str, ...] = SOP_KEYWORDS,
    ) -> dict[str, Any]:
        """把会议纪要/失败事件提炼入记忆，并产出 SOP 建议候选。

        返回：``{"notes": [...], "failures": [...], "sop_suggestions": [...],
        "proposals": [...]}``（均为记忆条目 id / 提案 id 列表）。
        """
        result: dict[str, list[str]] = {
            "notes": [],
            "failures": [],
            "sop_suggestions": [],
            "proposals": [],
        }
        notes = self._normalize_notes(meeting_notes)
        for note in notes:
            title = _title_from_text(note)
            item_id = self.memory.add_candidate(
                title=title,
                content=note,
                source=source,
                tier=Tier.PROJECT,
                session_id=session_id,
                meta={"kind": "meeting_note", "captured_by": "evolution_bridge"},
            )
            result["notes"].append(item_id)
            if session_id:
                self.memory.add_evidence(item_id, session_id, "会议纪要提炼")
            for suggestion in self._extract_sop_suggestions(note, sop_keywords):
                sug_id = self.memory.add_candidate(
                    title=suggestion,
                    content=f"{note}\n\n（提炼自会议纪要，来源 {source}）",
                    source="meeting",
                    tier=Tier.PROJECT,
                    session_id=session_id,
                    meta={"kind": "sop_suggestion"},
                )
                result["sop_suggestions"].append(sug_id)
                proposal_id = self.memory.create_proposal(
                    sug_id,
                    target_tier=Tier.PROJECT.value,
                    reason="会议纪要提炼 SOP 建议，待评审确认后入项目记忆",
                )
                result["proposals"].append(proposal_id)

        for event in events or []:
            payload = event.payload if hasattr(event, "payload") else event.get("payload", {})
            event_type = event.type if hasattr(event, "type") else event.get("type", "")
            if not isinstance(payload, dict):
                payload = {}
            if event_type == "retro" and payload.get("root_cause"):
                root = payload["root_cause"]
                roots = root if isinstance(root, list) else [root]
                for cause in roots:
                    text = str(cause).strip()
                    if not text:
                        continue
                    fail_id = self.memory.add_candidate(
                        title=_title_from_text(text),
                        content=f"复盘根因：{text}",
                        source=source,
                        tier=Tier.PROJECT,
                        session_id=session_id,
                        meta={"kind": "retro", "category": "knowledge"},
                    )
                    result["failures"].append(fail_id)
                    if session_id:
                        self.memory.add_evidence(fail_id, session_id, "复盘根因")
            elif event_type == "review_result" and str(payload.get("verdict", "")).lower() in (
                "reject",
                "rejected",
                "lbtm",
            ):
                target = payload.get("target") or "review"
                fail_id = self.memory.add_candidate(
                    title=f"评审驳回：{target}",
                    content=f"评审驳回：{target}（verdict={payload.get('verdict')}，reason={payload.get('reason', '')}）",
                    source="review",
                    tier=Tier.PROJECT,
                    session_id=session_id,
                    meta={"kind": "failure", "category": "skill"},
                )
                result["failures"].append(fail_id)
                if session_id:
                    self.memory.add_evidence(fail_id, session_id, "评审驳回")
        return result

    @staticmethod
    def _normalize_notes(meeting_notes: str | list[str] | list[dict] | None) -> list[str]:
        if not meeting_notes:
            return []
        if isinstance(meeting_notes, str):
            return [meeting_notes]
        result: list[str] = []
        for note in meeting_notes:
            if isinstance(note, str):
                result.append(note)
            elif isinstance(note, dict):
                result.append(json.dumps(note, ensure_ascii=False))
            else:
                result.append(str(note))
        return result

    def _extract_sop_suggestions(self, text: str, keywords: tuple[str, ...]) -> list[str]:
        suggestions: list[str] = []
        for line in text.splitlines():
            stripped = line.strip().strip("#").strip()
            if len(stripped) < 4:
                continue
            lowered = stripped.lower()
            if any(keyword.lower() in lowered for keyword in keywords):
                if stripped not in suggestions:
                    suggestions.append(stripped)
            if len(suggestions) >= 3:
                break
        return suggestions

    # ------------------------------------------------------------------
    # ④ 评审门 → 生效 / 回滚 + SOP 同步
    # ------------------------------------------------------------------

    def apply_proposal(
        self,
        proposal_id: str,
        *,
        approver: str = "governance",
        human_required: bool = False,
        auto_mode: str = "ask",
        reason: str = "进化提案自动评审通过",
    ) -> dict[str, Any]:
        """评审（approve）→ 生效 → 同步 SOP 变更记录。

        process/organization 类提案在 ``human_required=True`` 且
        ``auto_mode != 'ask'`` 时被引擎自动驳回（bypass-immune），
        返回的提案状态为 rejected，不会生效。
        """
        data = self._proposals.get(proposal_id)
        if not data:
            raise KeyError(f"提案不存在：{proposal_id}")
        proposal = EvolutionProposal(**data)
        self.engine.review(
            proposal,
            approver=approver,
            human_required=human_required,
            auto_mode=auto_mode,
            decision="approve",
            reason=reason,
        )
        if proposal.status != "approved":
            self._proposals[proposal_id] = proposal.model_dump(mode="json")
            self._save()
            return proposal.model_dump(mode="json")
        self.engine.apply(proposal)
        self._proposals[proposal_id] = proposal.model_dump(mode="json")
        self._save()
        if proposal.category in ("process", "organization"):
            self._sync_sop(proposal)
        return proposal.model_dump(mode="json")

    def rollback_proposal(
        self,
        proposal_id: str,
        *,
        reason: str = "观察期发现回归，回滚该进化提案",
    ) -> dict[str, Any]:
        """回滚已生效提案（仅 applied 可回滚；写审计事件）。"""
        data = self._proposals.get(proposal_id)
        if not data:
            raise KeyError(f"提案不存在：{proposal_id}")
        proposal = EvolutionProposal(**data)
        self.engine.rollback(proposal, reason=reason)
        self._proposals[proposal_id] = proposal.model_dump(mode="json")
        self._save()
        return proposal.model_dump(mode="json")

    def list_proposals(self, status: str | None = None) -> list[dict]:
        """列出提案（可过滤状态 draft/voting/approved/rejected/applied/rolled_back）。"""
        items = list(self._proposals.values())
        if status:
            items = [item for item in items if item.get("status") == status]
        items.sort(key=lambda item: str(item.get("created_ts", "")), reverse=True)
        return items

    def _sync_sop(self, proposal: EvolutionProposal) -> None:
        """把已生效的 process/organization 提案追加到 SOP 变更记录。"""
        path = self.sop_path
        header = "# SOP 变更记录（自动维护）\n\n"
        entry = (
            f"## {proposal.effective_version} ｜ {_now_iso()} ｜ {proposal.title}\n"
            f"- 类别：{proposal.category} ｜ 目标：{proposal.target} ｜ 风险：{proposal.risk_level}\n"
            f"- 变更内容：{json.dumps(proposal.change_diff, ensure_ascii=False)}\n"
            f"- 验证方案：{proposal.validation_plan or '（未填写）'}\n"
            f"- 回滚方案：{proposal.rollback_plan}\n"
            f"- 提案 id：`{proposal.id}`（状态 applied，灰度 {proposal.gray}）\n\n"
        )
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if not existing.startswith("# SOP"):
                existing = header + existing
            path.write_text(existing.rstrip() + "\n\n" + entry, encoding="utf-8")
        else:
            path.write_text(header + entry, encoding="utf-8")