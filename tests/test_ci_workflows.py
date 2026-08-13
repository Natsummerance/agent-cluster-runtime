"""T13.14 CI 工作流与 Actions 模板结构断言（pytest 直接读文件）。

- ci.yml 五个 job 齐全；package job GITHUB_TOKEN 显式置空（决策 D24）；
  release needs package + tag 门；e2e-real 含 serve 后台启动与 finally kill。
- agent-delivery 模板：environment: production 人工审批 + 不自动合并（负向断言）+ workflow_call 复用。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
TEMPLATE_YML = REPO_ROOT / ".github" / "workflow-templates" / "agent-delivery.yml"


def _job_block(text: str, job: str) -> str:
    """提取顶层 job 段落（缩进两格的 job 名起到下一个两格 job 名止）。"""
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if re.match(rf"^  {re.escape(job)}:$", line):
            start = idx
            break
    assert start is not None, f"job {job} 不存在"
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if re.match(r"^  [A-Za-z0-9_.-]+:$", line):
            break
        block.append(line)
    return "\n".join(block)


def test_ci_jobs_present():
    text = CI_YML.read_text(encoding="utf-8")
    for job in ("backend-test", "frontend-test", "e2e-real", "package", "release"):
        assert _job_block(text, job), f"缺少 job: {job}"


def test_package_token_emptied():
    text = CI_YML.read_text(encoding="utf-8")
    block = _job_block(text, "package")
    assert "GITHUB_TOKEN: ''" in block, "package job 必须显式 GITHUB_TOKEN: ''（决策 D24）"


def test_release_needs_package_and_tag_gate():
    text = CI_YML.read_text(encoding="utf-8")
    release = _job_block(text, "release")
    assert "needs: package" in release, "release 必须 needs: package"
    for job in ("package", "release"):
        block = _job_block(text, job)
        assert "startsWith(github.ref, 'refs/tags/v')" in block, f"{job} 必须带 tag 门"


def test_e2e_real_finally_kills_server():
    text = CI_YML.read_text(encoding="utf-8")
    block = _job_block(text, "e2e-real")
    assert "agent-cluster serve --port 8765 --auth-token ci" in block
    assert "SERVER_PID=$!" in block
    assert "finally" in block
    assert "kill" in block
    assert "trap cleanup EXIT" in block


def test_template_human_approval_and_no_merge():
    text = TEMPLATE_YML.read_text(encoding="utf-8")
    assert "environment: production" in text, "模板必须有人工审批 environment: production"
    # 负向断言：模板不得出现任何 merge/push（写默认分支）动作
    assert not re.search(r"\b(merge|push(?:-to-main)?)\b", text, flags=re.IGNORECASE), (
        "模板不得出现 merge/push 动作（不自动合并）"
    )


def test_template_workflow_call():
    text = TEMPLATE_YML.read_text(encoding="utf-8")
    ci_block = _job_block(text, "ci")
    assert "workflow_call" in ci_block or re.search(
        r"uses:\s+[^\s]+/\.github/workflows/ci\.yml", ci_block
    ), "模板 ci 步必须复用 ci.yml（workflow_call 语义）"