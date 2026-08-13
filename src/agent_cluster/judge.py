"""LLM-as-judge 质量门禁（v0.5 T12.6）。

- ``JudgeVerdict``：评审结论（pass / revise + 理由 + 建议），token 计入账本。
- ``GATE_ARTIFACT_MAP``：审批门类别 -> 工作区相对产物路径（评审对象）。
- ``LLMJudge.evaluate(kind, workspace)``：同步调用（内部线程 + 独立事件循环，
  可在 asyncio 会话线程内安全调用），评审产物并把模型用量经 ``on_usage`` 上报账本。
- 容错：模型不可用 / 输出无法解析时返回 ``pass`` + 说明理由，绝不阻断审批门。

评审要求模型只输出 JSON：``{"verdict": "pass"|"revise", "reason": "...", "suggestions": [...]}``。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent_cluster.models import AgentConfig, ModelConfig, TokenUsage

__all__ = [
    "JudgeVerdict",
    "GATE_ARTIFACT_MAP",
    "GATE_CONTEXT_MAP",
    "read_artifact",
    "parse_verdict",
    "LLMJudge",
]

# 审批门类别 -> 评审产物相对路径（"" 表示无单一文件，评审工作区摘要）
GATE_ARTIFACT_MAP: dict[str, str] = {
    "requirement_confirmation": "docs/PRD.md",
    "design_review": "docs/architecture.md",
    "iteration_acceptance": "",
    "release": "DELIVERY.md",
}

GATE_CONTEXT_MAP: dict[str, str] = {
    "requirement_confirmation": "需求文档评审：目标用户/范围/验收标准是否清晰完整，是否存在歧义与遗漏。",
    "design_review": "架构设计评审：模块划分、接口契约、技术选型、风险与演进路径是否合理。",
    "iteration_acceptance": "代码交付评审：可运行性、测试覆盖、代码质量与实现与设计的一致性。",
    "release": "发布交付评审：交付包完整性、部署说明、测试报告与验收记录是否满足发布条件。",
}

_KIND_LABELS: dict[str, str] = {
    "requirement_confirmation": "需求文档",
    "design_review": "架构设计",
    "iteration_acceptance": "代码交付",
    "release": "发布交付包",
}

_SYSTEM_PROMPT = (
    "你是资深评审工程师（LLM-as-judge）。请评审下面的产物，输出结论与改进建议。\n"
    "必须且只输出一个 JSON 对象（不要输出其他文字）：\n"
    '{"verdict": "pass" 或 "revise", "reason": "简要评审理由（<=120 字）", "suggestions": ["建议1", "建议2", ...]}\n'
    "verdict=pass 表示可以放行；verdict=revise 表示需要返工，reason 说明问题，suggestions 给出可执行建议。"
)


@dataclass
class JudgeVerdict:
    """一次 LLM 评审结论。"""

    verdict: str = "pass"
    reason: str = ""
    suggestions: list[str] = field(default_factory=list)
    raw: str = ""
    confidence: float = 1.0


def read_artifact(kind: str, workspace: str | Path) -> str | None:
    """读取评审产物文本；无对应产物或文件不存在返回 None。"""
    rel = GATE_ARTIFACT_MAP.get(kind)
    root = Path(workspace).expanduser().resolve()
    if rel:
        target = root / rel
        if target.is_file():
            try:
                return target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
        return None
    # 无单一文件：生成工作区摘要（文件树 + 代码规模）
    lines: list[str] = []
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".agent-cluster" in path.parts or ".git" in path.parts:
            continue
        if path.suffix not in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".md", ".yaml", ".yml", ".toml"):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        lines.append(f"{path.relative_to(root)} ({size} bytes)")
        count += 1
        if count >= 80:
            lines.append("...（文件过多，仅展示前 80 个）")
            break
    return "\n".join(lines) if lines else None


def _extract_json(text: str) -> dict | None:
    """从模型输出中提取 JSON 对象（支持 ```json fence 与裸 JSON）。"""
    if not text:
        return None
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidates = [candidate for candidate in fenced if candidate.strip()] or [text.strip()]
    for candidate in candidates:
        if not candidate.startswith("{"):
            continue
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def parse_verdict(text: str) -> JudgeVerdict:
    """解析评审 JSON；失败时返回 pass + 说明（不阻断门）。"""
    raw = (text or "").strip()
    data = _extract_json(raw)
    if data is None:
        return JudgeVerdict(verdict="pass", reason="评审输出无法解析，按放行处理", suggestions=[], raw=raw)
    verdict = str(data.get("verdict") or "pass").lower()
    if verdict not in ("pass", "revise"):
        verdict = "pass"
    suggestions = data.get("suggestions") or []
    if not isinstance(suggestions, list):
        suggestions = []
    suggestions = [str(item) for item in suggestions if str(item).strip()]
    try:
        confidence = float(data.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 1.0
    confidence = min(1.0, max(0.0, confidence))
    return JudgeVerdict(
        verdict=verdict,
        reason=str(data.get("reason") or "").strip(),
        suggestions=suggestions,
        raw=raw,
        confidence=confidence,
    )


class LLMJudge:
    """LLM-as-judge 门禁：评审产物 → Pass/Revise + 理由，用量经 on_usage 上报。"""

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str = "codex",
        timeout: float = 60.0,
        on_usage: Callable[[TokenUsage], None] | None = None,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.on_usage = on_usage
        if client is not None:
            self.client = client
        else:
            from agent_cluster.runtime import ChatModelFactory

            self.client = ChatModelFactory().create(AgentConfig(model=ModelConfig(model_name=model)))

    def _report_usage(self, usage: TokenUsage | None) -> None:
        if usage is not None and self.on_usage is not None:
            try:
                self.on_usage(usage)
            except Exception:  # noqa: BLE001 —— 记账失败不阻断
                pass

    async def _evaluate_async(
        self, kind: str, artifact_text: str, context: str = "", review_prompt: str = ""
    ) -> JudgeVerdict:
        kind_label = _KIND_LABELS.get(kind, kind)
        user = f"评审对象类别：{kind_label}\n评审要求：{GATE_CONTEXT_MAP.get(kind, '')}\n"
        if context:
            user += f"流程上下文：{context}\n"
        user += f"产物内容：\n{artifact_text or '（无产物文本，请基于上下文判断）'}"
        messages = [
            {"role": "system", "content": review_prompt.strip() or _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]
        response = await self.client.complete_with_tools(messages, tools=[])
        self._report_usage(response.usage)
        return parse_verdict(response.text)

    def evaluate(
        self, kind: str, workspace: str | Path, context: str = "", review_prompt: str = ""
    ) -> JudgeVerdict:
        """同步评审入口：独立线程 + 独立事件循环，可在运行中的 asyncio 会话线程内安全调用。

        `review_prompt` 非空时覆盖默认评审 system 提示词（v0.6 门策略）。
        """
        artifact_text = read_artifact(kind, workspace)
        result: dict[str, JudgeVerdict] = {}

        def _target() -> None:
            try:
                result["verdict"] = asyncio.run(
                    self._evaluate_async(kind, artifact_text or "", context, review_prompt)
                )
            except Exception as exc:  # noqa: BLE001 —— 评审失败不阻断门
                result["verdict"] = JudgeVerdict(
                    verdict="pass", reason=f"评审不可用：{exc}", suggestions=[]
                )

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        thread.join(timeout=self.timeout)
        if thread.is_alive():
            return JudgeVerdict(verdict="pass", reason=f"评审超时（>{self.timeout}s），按放行处理", suggestions=[])
        return result.get("verdict", JudgeVerdict(verdict="pass", reason="评审未产生结论", suggestions=[]))
