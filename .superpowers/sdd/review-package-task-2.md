# Task 2 Review Package

Base: c4f65a9
Head: 9b8e68c

## Diff stat

```
 examples/skills/backend-api-design/SKILL.md        |  15 +
 .../backend-api-design/assets/curl-example.txt     |   1 +
 .../backend-api-design/references/api-contract.md  |   6 +
 examples/skills/requirement-analysis/SKILL.md      |  14 +
 .../requirement-analysis/assets/example-prd.txt    |   1 +
 .../references/prd-template.md                     |   7 +
 .../requirement-analysis/scripts/checklist.py      |   8 +
 src/agent_cluster/__init__.py                      |  20 +-
 src/agent_cluster/models.py                        |   3 +
 src/agent_cluster/skills.py                        | 323 +++++++++++++++++++++
 tests/test_skills.py                               | 289 ++++++++++++++++++
 11 files changed, 685 insertions(+), 2 deletions(-)
```

## Full diff

```diff
diff --git a/examples/skills/backend-api-design/SKILL.md b/examples/skills/backend-api-design/SKILL.md
new file mode 100644
index 0000000..8746588
--- /dev/null
+++ b/examples/skills/backend-api-design/SKILL.md
@@ -0,0 +1,15 @@
+---
+name: backend-api-design
+description: 后端 API 设计技能：REST/OpenAPI 契约、错误码与幂等性设计。
+version: 2.1.0
+license: MIT
+allowed-tools:
+  - read_file
+  - write_file
+  - bash
+---
+# 后端 API 设计指引
+
+1. 先定义 OpenAPI 契约再实现。
+2. 统一错误码结构与错误响应体。
+3. 写操作需声明幂等键（Idempotency-Key）。
diff --git a/examples/skills/backend-api-design/assets/curl-example.txt b/examples/skills/backend-api-design/assets/curl-example.txt
new file mode 100644
index 0000000..e5ad35b
--- /dev/null
+++ b/examples/skills/backend-api-design/assets/curl-example.txt
@@ -0,0 +1 @@
+curl -X POST /api/v1/reports -H "Idempotency-Key: abc-123"
diff --git a/examples/skills/backend-api-design/references/api-contract.md b/examples/skills/backend-api-design/references/api-contract.md
new file mode 100644
index 0000000..102ad5b
--- /dev/null
+++ b/examples/skills/backend-api-design/references/api-contract.md
@@ -0,0 +1,6 @@
+# API 契约检查表
+
+- 资源命名（复数名词）
+- 状态码语义
+- 分页参数
+- 幂等性声明
diff --git a/examples/skills/requirement-analysis/SKILL.md b/examples/skills/requirement-analysis/SKILL.md
new file mode 100644
index 0000000..289dda0
--- /dev/null
+++ b/examples/skills/requirement-analysis/SKILL.md
@@ -0,0 +1,14 @@
+---
+name: requirement-analysis
+description: 需求分析与澄清技能：拆解 PRD、提取验收标准、识别边界条件与依赖。
+version: 1.0.0
+license: MIT
+allowed-tools:
+  - read_file
+  - write_file
+---
+# 需求分析执行指引
+
+1. 通读需求材料，列出事实清单与假设。
+2. 提取可验证的验收标准（Given/When/Then 格式）。
+3. 标注边界条件、外部依赖与未决问题，交给 PM 澄清。
diff --git a/examples/skills/requirement-analysis/assets/example-prd.txt b/examples/skills/requirement-analysis/assets/example-prd.txt
new file mode 100644
index 0000000..41a0b2e
--- /dev/null
+++ b/examples/skills/requirement-analysis/assets/example-prd.txt
@@ -0,0 +1 @@
+示例需求：用户可导出项目报告为 PDF。
diff --git a/examples/skills/requirement-analysis/references/prd-template.md b/examples/skills/requirement-analysis/references/prd-template.md
new file mode 100644
index 0000000..7f45294
--- /dev/null
+++ b/examples/skills/requirement-analysis/references/prd-template.md
@@ -0,0 +1,7 @@
+# PRD 拆解模板
+
+- 背景与目标
+- 用户故事
+- 验收标准
+- 边界条件
+- 依赖与风险
diff --git a/examples/skills/requirement-analysis/scripts/checklist.py b/examples/skills/requirement-analysis/scripts/checklist.py
new file mode 100644
index 0000000..cfac170
--- /dev/null
+++ b/examples/skills/requirement-analysis/scripts/checklist.py
@@ -0,0 +1,8 @@
+"""需求分析清单生成脚本（示例资源文件）。"""
+
+CHECKLIST = ["facts", "assumptions", "acceptance_criteria", "dependencies"]
+
+
+def build_checklist() -> list[str]:
+    """返回需求分析清单标题列表。"""
+    return CHECKLIST
diff --git a/src/agent_cluster/__init__.py b/src/agent_cluster/__init__.py
index 5c714fe..6220ad9 100644
--- a/src/agent_cluster/__init__.py
+++ b/src/agent_cluster/__init__.py
@@ -1,7 +1,7 @@
 """agent_cluster — 多 agent 组织型全栈开发集群运行时（Python + LangGraph）。
 
-当前阶段提供数据模型层（models.py）；后续任务将逐步加入技能层、流程引擎、
-审批门、组织角色、运行时、会议、进化闭环与 CLI。
+当前阶段提供数据模型层（models.py）与技能层（skills.py）；后续任务将逐步
+加入流程引擎、审批门、组织角色、运行时、会议、进化闭环与 CLI。
 """
 
 from agent_cluster.models import (
@@ -39,6 +39,15 @@ from agent_cluster.models import (
     TaskStatus,
     Vote,
 )
+from agent_cluster.skills import (
+    DisclosureLevel,
+    SkillCatalog,
+    SkillError,
+    SkillFrontmatter,
+    SkillLoader,
+    SkillRegistry,
+    format_skill_context,
+)
 
 __version__ = "0.1.0"
 
@@ -52,6 +61,7 @@ __all__ = [
     "ClusterState",
     "ContextConfig",
     "Decision",
+    "DisclosureLevel",
     "Event",
     "GateKind",
     "HumanInterruptConfig",
@@ -73,8 +83,14 @@ __all__ = [
     "Role",
     "RoleKind",
     "Skill",
+    "SkillCatalog",
+    "SkillError",
+    "SkillFrontmatter",
+    "SkillLoader",
+    "SkillRegistry",
     "Task",
     "TaskStatus",
     "Vote",
     "__version__",
+    "format_skill_context",
 ]
diff --git a/src/agent_cluster/models.py b/src/agent_cluster/models.py
index 5fc9296..7901b41 100644
--- a/src/agent_cluster/models.py
+++ b/src/agent_cluster/models.py
@@ -345,6 +345,9 @@ class Skill(BaseModel):
     version: str = Field(default="0.1.0", description="技能版本（semver）")
     description: str = Field(default="", description="技能描述")
     license: str | None = Field(default=None, description="许可证，None 表示未声明")
+    compatibility: str | None = Field(
+        default=None, description="平台版本约束（如 >=0.1.0），None 表示不限制"
+    )
     allowed_tools: list[str] | None = Field(default=None, description="工具白名单，None 表示不限制")
     dir: str = Field(default="", description="技能包目录路径")
     markdown: str = Field(default="", description="SKILL.md 正文内容")
diff --git a/src/agent_cluster/skills.py b/src/agent_cluster/skills.py
new file mode 100644
index 0000000..dd87aa8
--- /dev/null
+++ b/src/agent_cluster/skills.py
@@ -0,0 +1,323 @@
+"""技能层（§5.5）：SKILL.md 加载、注册与渐进披露。
+
+实现三个组件：
+- ``SkillLoader``：递归扫描目录树识别 ``SKILL.md``，解析 frontmatter（PyYAML
+  safe_load）+ 正文，资源文件按 scripts/references/assets 子目录分类。
+- ``SkillRegistry``：按 ``@org/name`` 源前缀注册，``name+version`` 去重，
+  并在注册时执行 ``compatibility`` 平台版本约束。
+- ``SkillCatalog``：按角色挂载 ``Role.skills``（name@version）指定的技能，
+  并计算技能 allowed_tools 与角色 tools 的交集。
+
+渐进披露：``DisclosureLevel`` 1/2/3（仅 frontmatter 建目录 / 加载正文 /
+登记资源文件），``format_skill_context`` 输出 ``<skill name="...">`` 稳定锚块，
+仅在需要时提升披露级别，避免污染上下文。
+"""
+
+from __future__ import annotations
+
+from enum import IntEnum
+from pathlib import Path
+from typing import Iterable
+
+import yaml
+from pydantic import BaseModel, ConfigDict, Field, ValidationError
+
+from agent_cluster.models import Role, Skill
+
+__all__ = [
+    "SkillError",
+    "DisclosureLevel",
+    "SkillFrontmatter",
+    "format_skill_context",
+    "SkillLoader",
+    "SkillRegistry",
+    "SkillCatalog",
+]
+
+
+class SkillError(Exception):
+    """技能层统一异常：解析失败、注册冲突、兼容性不满足、未找到等。"""
+
+
+class DisclosureLevel(IntEnum):
+    """渐进披露级别（§5.5）。
+
+    - LEVEL_1 = 1：仅 frontmatter（目录级信息，建目录即可）。
+    - LEVEL_2 = 2：额外加载 SKILL.md 正文（执行指令）。
+    - LEVEL_3 = 3：额外登记 scripts/references/assets 资源文件清单。
+    """
+
+    LEVEL_1 = 1
+    LEVEL_2 = 2
+    LEVEL_3 = 3
+
+
+class SkillFrontmatter(BaseModel):
+    """SKILL.md frontmatter 契约（对齐 anthropic SKILL.md 约定）。
+
+    ``name``/``description`` 必填；``license``/``compatibility``/``version``/
+    ``allowed_tools`` 可选。``allowed-tools``（kebab-case）与
+    ``metadata.version``（嵌套）在加载时归一化到本模型字段。
+    """
+
+    model_config = ConfigDict(extra="ignore")
+
+    name: str = Field(
+        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", description="技能名称（小写连字符）"
+    )
+    description: str = Field(max_length=1024, description="技能描述（≤1024 字符）")
+    license: str | None = Field(default=None, description="许可证，None 表示未声明")
+    compatibility: str | None = Field(
+        default=None, description="平台版本约束（如 >=0.1.0 或逗号分隔多值），None 表示不限制"
+    )
+    version: str | None = Field(default=None, description="技能版本（semver），None 时回退 0.1.0")
+    allowed_tools: list[str] | None = Field(default=None, description="工具白名单，None 表示不限制")
+
+
+def _normalize_frontmatter(data: dict) -> dict:
+    """把 SKILL.md 常见写法归一化到 SkillFrontmatter 字段名。"""
+    normalized = dict(data)
+    if "allowed-tools" in normalized and "allowed_tools" not in normalized:
+        normalized["allowed_tools"] = normalized.pop("allowed-tools")
+    metadata = normalized.get("metadata")
+    if isinstance(metadata, dict) and "version" in metadata and "version" not in normalized:
+        normalized["version"] = metadata["version"]
+    return normalized
+
+
+def _version_key(version: str) -> tuple[int, ...]:
+    """把 semver 字符串转成可比较的整数元组；非数字段按 0 处理。"""
+    parts: list[int] = []
+    for part in version.split("."):
+        try:
+            parts.append(int(part))
+        except ValueError:
+            parts.append(0)
+    return tuple(parts)
+
+
+def _normalize_source(source: str | None) -> str:
+    """归一化注册源为 ``@org/`` 前缀；空源返回空串。"""
+    if not source:
+        return ""
+    source = source.strip()
+    if not source.startswith("@"):
+        raise SkillError(f"注册源必须以 @ 开头（如 @acme），收到：{source!r}")
+    return f"{source.rstrip('/')}/"
+
+
+def format_skill_context(skill: Skill, level: int | DisclosureLevel) -> str:
+    """按披露级别输出 ``<skill name="...">`` 稳定锚块。
+
+    - level 1：仅 frontmatter 信息（name/version/description/license/allowed_tools）。
+    - level 2：追加 ``<body>`` 正文 markdown。
+    - level 3：追加 ``<resources>`` 资源文件清单（scripts/references/assets）。
+    """
+    level_value = int(level)
+    if level_value not in (1, 2, 3):
+        raise ValueError(f"非法披露级别：{level}，仅支持 1/2/3")
+    lines = [f'<skill name="{skill.name}" version="{skill.version}">']
+    lines.append(f"<description>{skill.description}</description>")
+    if skill.license:
+        lines.append(f"<license>{skill.license}</license>")
+    if skill.allowed_tools:
+        lines.append(f"<allowed_tools>{', '.join(skill.allowed_tools)}</allowed_tools>")
+    if level_value >= DisclosureLevel.LEVEL_2:
+        lines.append("<body>")
+        lines.append(skill.markdown)
+        lines.append("</body>")
+    if level_value >= DisclosureLevel.LEVEL_3:
+        lines.append("<resources>")
+        for category in ("scripts", "references", "assets"):
+            files = skill.resource_files.get(category, [])
+            if files:
+                lines.append(f"{category}: " + ", ".join(files))
+        lines.append("</resources>")
+    lines.append("</skill>")
+    return "\n".join(lines)
+
+
+class SkillLoader:
+    """SKILL.md 目录扫描与加载。
+
+    ``load(dir)`` 完整加载一个技能包（frontmatter + 正文 + 资源分类，disclosure_level=3）；
+    ``list_skills(root)`` 递归扫描目录树，识别所有 ``SKILL.md`` 并逐一加载。
+    非法 frontmatter 一律抛 ``SkillError``。
+    """
+
+    def list_skills(self, root: str | Path) -> list[Skill]:
+        """递归扫描 ``root`` 目录树，返回所有 SKILL.md 对应的 Skill 对象（按路径排序）。"""
+        root_path = Path(root)
+        if not root_path.is_dir():
+            raise SkillError(f"技能根目录不存在：{root_path}")
+        skills: list[Skill] = []
+        for skill_md in sorted(root_path.rglob("SKILL.md")):
+            rel_parts = skill_md.relative_to(root_path).parts
+            if any(part.startswith(".") for part in rel_parts):
+                continue
+            skills.append(self.load(skill_md.parent))
+        return skills
+
+    def load(self, dir_path: str | Path) -> Skill:
+        """解析单个技能包目录：frontmatter + 正文 markdown + 资源文件分类。"""
+        package_dir = Path(dir_path)
+        skill_md = package_dir / "SKILL.md"
+        if not skill_md.is_file():
+            raise SkillError(f"技能目录缺少 SKILL.md：{package_dir}")
+        text = skill_md.read_text(encoding="utf-8")
+        frontmatter_data, body = self._parse_skill_md(text, skill_md)
+        try:
+            fm = SkillFrontmatter.model_validate(_normalize_frontmatter(frontmatter_data))
+        except ValidationError as exc:
+            raise SkillError(f"非法 frontmatter（{skill_md}）：{exc}") from exc
+        resource_files = self._classify_resources(package_dir)
+        return Skill(
+            name=fm.name,
+            version=fm.version or "0.1.0",
+            description=fm.description,
+            license=fm.license,
+            allowed_tools=fm.allowed_tools,
+            dir=str(package_dir.resolve()),
+            markdown=body,
+            disclosure_level=DisclosureLevel.LEVEL_3,
+            resource_files=resource_files,
+        )
+
+    @staticmethod
+    def _parse_skill_md(text: str, source: Path) -> tuple[dict, str]:
+        """解析 ``---`` frontmatter 块与正文；frontmatter 必须位于文件开头且闭合。"""
+        if not text.startswith("---"):
+            raise SkillError(f"缺少 frontmatter 块（须以 --- 开头）：{source}")
+        lines = text.splitlines()
+        end_index = None
+        for index in range(1, len(lines)):
+            if lines[index].strip() == "---":
+                end_index = index
+                break
+        if end_index is None:
+            raise SkillError(f"frontmatter 块未闭合（缺少结尾 ---）：{source}")
+        frontmatter_text = "\n".join(lines[1:end_index])
+        body = "\n".join(lines[end_index + 1 :]).strip()
+        try:
+            data = yaml.safe_load(frontmatter_text)
+        except yaml.YAMLError as exc:
+            raise SkillError(f"frontmatter YAML 解析失败（{source}）：{exc}") from exc
+        if not isinstance(data, dict):
+            raise SkillError(f"frontmatter 必须是 YAML 映射（{source}），收到：{type(data).__name__}")
+        return data, body
+
+    @staticmethod
+    def _classify_resources(package_dir: Path) -> dict[str, list[str]]:
+        """把 scripts/references/assets 子目录下的文件按相对路径分类。"""
+        classified: dict[str, list[str]] = {}
+        for category in ("scripts", "references", "assets"):
+            sub_dir = package_dir / category
+            if not sub_dir.is_dir():
+                continue
+            files = sorted(
+                str(path.relative_to(package_dir)).replace("\\", "/")
+                for path in sub_dir.rglob("*")
+                if path.is_file()
+            )
+            if files:
+                classified[category] = files
+        return classified
+
+
+class SkillRegistry:
+    """技能注册表：源前缀命名空间 + name@version 去重 + compatibility 约束。
+
+    - ``register(skill, source)``：source 形如 ``@org``（归一化为 ``@org/``），
+      注册键为 ``{source}{skill.name}@{skill.version}``；同键重复注册抛 ``SkillError``。
+    - ``get(name, version=None)``：name 可带 ``@org/`` 前缀；version 缺省返回最高版本。
+    - ``list()``：按注册键排序返回全部已注册技能。
+    """
+
+    def __init__(self, platform_version: str = "0.1.0"):
+        self.platform_version = platform_version
+        self._skills: dict[str, Skill] = {}
+
+    def register(self, skill: Skill, source: str | None = "") -> str:
+        """注册技能；同 name+version 重复注册或 compatibility 不满足时抛 SkillError。"""
+        prefix = _normalize_source(source)
+        key = f"{prefix}{skill.name}@{skill.version}"
+        if key in self._skills:
+            raise SkillError(f"技能已注册（name+version 去重）：{key}")
+        self._check_compatibility(skill)
+        self._skills[key] = skill
+        return key
+
+    def get(self, name: str, version: str | None = None) -> Skill:
+        """按名称（可带 ``@org/`` 前缀）查询技能；version 缺省返回最高版本。"""
+        name = name.strip()
+        candidates = [
+            skill
+            for key, skill in self._skills.items()
+            if self._split_key(key)[0] == name
+            and (version is None or self._split_key(key)[1] == version)
+        ]
+        if not candidates:
+            suffix = version or "*"
+            raise SkillError(f"未注册技能：{name}@{suffix}")
+        if version is not None:
+            return candidates[0]
+        return max(candidates, key=lambda skill: _version_key(skill.version))
+
+    def list(self) -> list[Skill]:
+        """返回全部已注册技能，按注册键排序。"""
+        return [self._skills[key] for key in sorted(self._skills)]
+
+    def _check_compatibility(self, skill: Skill) -> None:
+        """compatibility 约束：精确版本或 ``>=x.y.z``（逗号分隔多值），全部不满足则报错。"""
+        if not skill.compatibility:
+            return
+        for spec in (part.strip() for part in skill.compatibility.split(",")):
+            if not spec:
+                continue
+            if spec.startswith(">="):
+                if _version_key(self.platform_version) >= _version_key(spec[2:].strip()):
+                    return
+            elif spec == self.platform_version:
+                return
+        raise SkillError(
+            f"技能 {skill.name} 兼容性 {skill.compatibility!r} 不满足平台版本 {self.platform_version!r}"
+        )
+
+    @staticmethod
+    def _split_key(key: str) -> tuple[str, str]:
+        """把注册键拆成（限定名, 版本）。"""
+        qualified, _, version = key.rpartition("@")
+        return qualified, version
+
+
+class SkillCatalog:
+    """按角色挂载的技能目录。
+
+    ``mount(role, skills)`` 只挂载 ``Role.skills`` 中以 ``name@version`` 指定的技能；
+    ``allowed_tools(role)`` 返回技能 allowed_tools 与角色 tools 的交集
+    （技能 allowed_tools 为 None 表示不限制，放行全部角色工具）。
+    """
+
+    def __init__(self) -> None:
+        self._mounted: dict[str, list[Skill]] = {}
+
+    def mount(self, role: Role, skills: Iterable[Skill]) -> list[Skill]:
+        """挂载角色技能清单中出现的技能，返回实际挂载列表并缓存到目录。"""
+        wanted = set(role.skills)
+        mounted = [skill for skill in skills if f"{skill.name}@{skill.version}" in wanted]
+        self._mounted[role.id] = mounted
+        return mounted
+
+    def mounted_skills(self, role: Role) -> list[Skill]:
+        """返回该角色已挂载的技能列表（未挂载返回空列表）。"""
+        return list(self._mounted.get(role.id, []))
+
+    def allowed_tools(self, role: Role) -> list[str]:
+        """返回技能 allowed_tools 与角色 tools 的交集（按名称排序）。"""
+        allowed: set[str] = set()
+        for skill in self._mounted.get(role.id, []):
+            if skill.allowed_tools is None:
+                allowed.update(role.tools)
+            else:
+                allowed.update(set(skill.allowed_tools) & set(role.tools))
+        return sorted(allowed)
diff --git a/tests/test_skills.py b/tests/test_skills.py
new file mode 100644
index 0000000..2368266
--- /dev/null
+++ b/tests/test_skills.py
@@ -0,0 +1,289 @@
+"""Task 2 技能层行为测试。
+
+覆盖：示例技能解析（frontmatter/正文/资源分类）、缺 name 报错、name+version
+去重与兼容性约束、@org/name 源前缀、按角色挂载交集、三级渐进披露内容差异。
+"""
+
+from pathlib import Path
+
+import pytest
+
+from agent_cluster.models import Role, RoleKind, Skill
+from agent_cluster.skills import (
+    DisclosureLevel,
+    SkillCatalog,
+    SkillError,
+    SkillFrontmatter,
+    SkillLoader,
+    SkillRegistry,
+    format_skill_context,
+)
+
+REPO_ROOT = Path(__file__).resolve().parents[1]
+EXAMPLES_SKILLS = REPO_ROOT / "examples" / "skills"
+
+
+def make_role(role_id: str, skills: list[str], tools: list[str]) -> Role:
+    """构造最小可用的 Role 对象。"""
+    return Role(
+        id=role_id,
+        name=role_id,
+        kind=RoleKind.PM,
+        goal="测试岗位",
+        backstory="测试岗位背景",
+        skills=skills,
+        tools=tools,
+    )
+
+
+def make_skill(name: str, version: str, compatibility: str | None = None) -> Skill:
+    return Skill(
+        name=name,
+        version=version,
+        description=f"{name} 描述",
+        compatibility=compatibility,
+        allowed_tools=["read_file"],
+    )
+
+
+# ---------------------------------------------------------------------------
+# 示例技能解析
+# ---------------------------------------------------------------------------
+
+
+def test_list_skills_loads_at_least_two_example_skills():
+    loader = SkillLoader()
+    skills = loader.list_skills(EXAMPLES_SKILLS)
+    assert len(skills) >= 2
+    names = {skill.name for skill in skills}
+    assert {"requirement-analysis", "backend-api-design"} <= names
+
+
+def test_load_example_skill_parses_frontmatter_and_resources():
+    loader = SkillLoader()
+    skill = loader.load(EXAMPLES_SKILLS / "requirement-analysis")
+    assert skill.name == "requirement-analysis"
+    assert skill.version == "1.0.0"
+    assert "需求分析" in skill.description
+    assert skill.license == "MIT"
+    assert skill.allowed_tools == ["read_file", "write_file"]
+    assert skill.markdown.startswith("# 需求分析执行指引")
+    assert skill.dir.endswith("requirement-analysis")
+    assert skill.disclosure_level == DisclosureLevel.LEVEL_3
+    assert skill.resource_files["scripts"] == ["scripts/checklist.py"]
+    assert skill.resource_files["references"] == ["references/prd-template.md"]
+    assert skill.resource_files["assets"] == ["assets/example-prd.txt"]
+
+
+def test_load_normalizes_kebab_case_and_metadata_version(tmp_path: Path):
+    package = tmp_path / "sample-skill"
+    package.mkdir()
+    (package / "SKILL.md").write_text(
+        "---\n"
+        "name: sample-skill\n"
+        "description: 示例技能\n"
+        "allowed-tools:\n"
+        "  - read_file\n"
+        "metadata:\n"
+        "  version: 3.2.1\n"
+        "---\n正文",
+        encoding="utf-8",
+    )
+    skill = SkillLoader().load(package)
+    assert skill.allowed_tools == ["read_file"]
+    assert skill.version == "3.2.1"
+
+
+def test_skill_frontmatter_required_fields():
+    fm = SkillFrontmatter(name="sample-skill", description="示例")
+    assert fm.version is None
+    assert fm.allowed_tools is None
+    assert fm.compatibility is None
+
+
+def test_load_skill_without_version_defaults_to_0_1_0(tmp_path: Path):
+    package = tmp_path / "no-version"
+    package.mkdir()
+    (package / "SKILL.md").write_text(
+        "---\nname: no-version\ndescription: 无版本技能\n---\n正文", encoding="utf-8"
+    )
+    skill = SkillLoader().load(package)
+    assert skill.version == "0.1.0"
+
+
+# ---------------------------------------------------------------------------
+# 非法 frontmatter
+# ---------------------------------------------------------------------------
+
+
+def test_load_missing_name_raises_skill_error(tmp_path: Path):
+    package = tmp_path / "no-name"
+    package.mkdir()
+    (package / "SKILL.md").write_text(
+        "---\ndescription: 缺少 name\n---\n正文", encoding="utf-8"
+    )
+    with pytest.raises(SkillError, match="name"):
+        SkillLoader().load(package)
+
+
+def test_load_missing_frontmatter_raises_skill_error(tmp_path: Path):
+    package = tmp_path / "no-frontmatter"
+    package.mkdir()
+    (package / "SKILL.md").write_text("没有 frontmatter 的正文", encoding="utf-8")
+    with pytest.raises(SkillError, match="frontmatter"):
+        SkillLoader().load(package)
+
+
+def test_load_invalid_yaml_raises_skill_error(tmp_path: Path):
+    package = tmp_path / "bad-yaml"
+    package.mkdir()
+    (package / "SKILL.md").write_text(
+        "---\nname: [unclosed\n---\n正文", encoding="utf-8"
+    )
+    with pytest.raises(SkillError):
+        SkillLoader().load(package)
+
+
+# ---------------------------------------------------------------------------
+# 注册表：去重 / 源前缀 / 版本 / 兼容性
+# ---------------------------------------------------------------------------
+
+
+def test_register_dedupe_by_name_and_version():
+    registry = SkillRegistry()
+    registry.register(make_skill("req-analysis", "1.0.0"))
+    with pytest.raises(SkillError, match="去重"):
+        registry.register(make_skill("req-analysis", "1.0.0"))
+    registry.register(make_skill("req-analysis", "1.1.0"))
+    assert len(registry.list()) == 2
+
+
+def test_register_source_prefix_and_get():
+    registry = SkillRegistry()
+    registry.register(make_skill("req-analysis", "1.0.0"), source="@acme")
+    skill = registry.get("@acme/req-analysis")
+    assert skill.name == "req-analysis"
+    with pytest.raises(SkillError):
+        registry.get("req-analysis")
+    assert [s.name for s in registry.list()] == ["req-analysis"]
+
+
+def test_get_without_version_returns_highest():
+    registry = SkillRegistry()
+    registry.register(make_skill("req-analysis", "1.0.0"))
+    registry.register(make_skill("req-analysis", "2.3.0"))
+    assert registry.get("req-analysis").version == "2.3.0"
+    assert registry.get("req-analysis", version="1.0.0").version == "1.0.0"
+    with pytest.raises(SkillError):
+        registry.get("req-analysis", version="9.9.9")
+
+
+def test_register_enforces_compatibility_constraint():
+    registry = SkillRegistry(platform_version="0.1.0")
+    with pytest.raises(SkillError, match="兼容性"):
+        registry.register(make_skill("too-new", "1.0.0", compatibility=">=9.9.9"))
+    registry.register(make_skill("exact-ok", "1.0.0", compatibility="0.1.0"))
+    registry.register(make_skill("range-ok", "1.0.0", compatibility=">=0.1.0, <=1.0.0"))
+    registry.register(make_skill("unconstrained", "1.0.0"))
+    assert len(registry.list()) == 3
+
+
+def test_register_rejects_invalid_source_prefix():
+    registry = SkillRegistry()
+    with pytest.raises(SkillError, match="@"):
+        registry.register(make_skill("req-analysis", "1.0.0"), source="acme")
+
+
+# ---------------------------------------------------------------------------
+# 按角色挂载与工具交集
+# ---------------------------------------------------------------------------
+
+
+def test_mount_only_skills_listed_in_role():
+    loader = SkillLoader()
+    skills = loader.list_skills(EXAMPLES_SKILLS)
+    role = make_role(
+        role_id="pm",
+        skills=["requirement-analysis@1.0.0"],
+        tools=["read_file", "write_file"],
+    )
+    catalog = SkillCatalog()
+    mounted = catalog.mount(role, skills)
+    assert [skill.name for skill in mounted] == ["requirement-analysis"]
+    assert [skill.name for skill in catalog.mounted_skills(role)] == ["requirement-analysis"]
+
+
+def test_allowed_tools_intersection_with_role_tools():
+    loader = SkillLoader()
+    skills = loader.list_skills(EXAMPLES_SKILLS)
+    role = make_role(
+        role_id="backend",
+        skills=["backend-api-design@2.1.0"],
+        tools=["read_file", "bash", "search"],
+    )
+    catalog = SkillCatalog()
+    catalog.mount(role, skills)
+    # backend-api-design allowed_tools=[read_file, write_file, bash] ∩ role tools
+    assert catalog.allowed_tools(role) == ["bash", "read_file"]
+
+
+def test_allowed_tools_unrestricted_skill_passes_all_role_tools():
+    unrestricted = make_skill("unrestricted", "1.0.0")
+    unrestricted.allowed_tools = None
+    role = make_role(
+        role_id="pm",
+        skills=["unrestricted@1.0.0"],
+        tools=["read_file", "bash"],
+    )
+    catalog = SkillCatalog()
+    catalog.mount(role, [unrestricted])
+    assert catalog.allowed_tools(role) == ["bash", "read_file"]
+
+
+# ---------------------------------------------------------------------------
+# 三级渐进披露
+# ---------------------------------------------------------------------------
+
+
+def test_format_skill_context_three_levels_increase_content():
+    loader = SkillLoader()
+    skill = loader.load(EXAMPLES_SKILLS / "requirement-analysis")
+    level1 = format_skill_context(skill, DisclosureLevel.LEVEL_1)
+    level2 = format_skill_context(skill, DisclosureLevel.LEVEL_2)
+    level3 = format_skill_context(skill, DisclosureLevel.LEVEL_3)
+
+    assert level1.startswith('<skill name="requirement-analysis"')
+    assert level1.count("</skill>") == 1
+    assert "<description>" in level1
+    assert "<body>" not in level1
+    assert "<resources>" not in level1
+
+    assert "<body>" in level2
+    assert "需求分析执行指引" in level2
+    assert "<resources>" not in level2
+    # level 2 保留 level 1 的 frontmatter 区块
+    assert '<skill name="requirement-analysis"' in level2
+    assert "<description>" in level2
+    assert "<license>" in level2
+    assert "<allowed_tools>" in level2
+
+    assert "<resources>" in level3
+    assert "scripts/checklist.py" in level3
+    assert "references/prd-template.md" in level3
+    assert "assets/example-prd.txt" in level3
+    # level 3 保留 level 2 的正文区块
+    assert "<body>" in level3
+    assert "需求分析执行指引" in level3
+
+
+def test_format_skill_context_accepts_int_level():
+    loader = SkillLoader()
+    skill = loader.load(EXAMPLES_SKILLS / "backend-api-design")
+    assert "<body>" in format_skill_context(skill, 2)
+
+
+def test_format_skill_context_rejects_invalid_level():
+    loader = SkillLoader()
+    skill = loader.load(EXAMPLES_SKILLS / "backend-api-design")
+    with pytest.raises(ValueError):
+        format_skill_context(skill, 4)
```
