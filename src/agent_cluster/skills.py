"""技能层（§5.5）：SKILL.md 加载、注册与渐进披露。

实现三个组件：
- ``SkillLoader``：递归扫描目录树识别 ``SKILL.md``，解析 frontmatter（PyYAML
  safe_load）+ 正文，资源文件按 scripts/references/assets 子目录分类。
- ``SkillRegistry``：按 ``@org/name`` 源前缀注册，``name+version`` 去重，
  并在注册时执行 ``compatibility`` 平台版本约束。
- ``SkillCatalog``：按角色挂载 ``Role.skills``（name@version）指定的技能，
  并计算技能 allowed_tools 与角色 tools 的交集。

渐进披露：``DisclosureLevel`` 1/2/3（仅 frontmatter 建目录 / 加载正文 /
登记资源文件），``format_skill_context`` 输出 ``<skill name="...">`` 稳定锚块，
仅在需要时提升披露级别，避免污染上下文。
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_cluster.models import Role, Skill

__all__ = [
    "SkillError",
    "DisclosureLevel",
    "SkillFrontmatter",
    "format_skill_context",
    "SkillLoader",
    "SkillRegistry",
    "SkillCatalog",
]


class SkillError(Exception):
    """技能层统一异常：解析失败、注册冲突、兼容性不满足、未找到等。"""


class DisclosureLevel(IntEnum):
    """渐进披露级别（§5.5）。

    - LEVEL_1 = 1：仅 frontmatter（目录级信息，建目录即可）。
    - LEVEL_2 = 2：额外加载 SKILL.md 正文（执行指令）。
    - LEVEL_3 = 3：额外登记 scripts/references/assets 资源文件清单。
    """

    LEVEL_1 = 1
    LEVEL_2 = 2
    LEVEL_3 = 3


class SkillFrontmatter(BaseModel):
    """SKILL.md frontmatter 契约（对齐 anthropic SKILL.md 约定）。

    ``name``/``description`` 必填；``license``/``compatibility``/``version``/
    ``allowed_tools`` 可选。``allowed-tools``（kebab-case）与
    ``metadata.version``（嵌套）在加载时归一化到本模型字段。
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", description="技能名称（小写连字符）"
    )
    description: str = Field(max_length=1024, description="技能描述（≤1024 字符）")
    license: str | None = Field(default=None, description="许可证，None 表示未声明")
    compatibility: str | None = Field(
        default=None, description="平台版本约束（如 >=0.1.0 或逗号分隔多值），None 表示不限制"
    )
    version: str | None = Field(default=None, description="技能版本（semver），None 时回退 0.1.0")
    allowed_tools: list[str] | None = Field(default=None, description="工具白名单，None 表示不限制")


def _normalize_frontmatter(data: dict) -> dict:
    """把 SKILL.md 常见写法归一化到 SkillFrontmatter 字段名。"""
    normalized = dict(data)
    if "allowed-tools" in normalized and "allowed_tools" not in normalized:
        normalized["allowed_tools"] = normalized.pop("allowed-tools")
    metadata = normalized.get("metadata")
    if isinstance(metadata, dict) and "version" in metadata and "version" not in normalized:
        normalized["version"] = metadata["version"]
    return normalized


def _version_key(version: str) -> tuple[int, ...]:
    """把 semver 字符串转成可比较的整数元组；非数字段按 0 处理。"""
    parts: list[int] = []
    for part in version.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _normalize_source(source: str | None) -> str:
    """归一化注册源为 ``@org/`` 前缀；空源返回空串。"""
    if not source:
        return ""
    source = source.strip()
    if not source.startswith("@"):
        raise SkillError(f"注册源必须以 @ 开头（如 @acme），收到：{source!r}")
    return f"{source.rstrip('/')}/"


def format_skill_context(skill: Skill, level: int | DisclosureLevel) -> str:
    """按披露级别输出 ``<skill name="...">`` 稳定锚块。

    - level 1：仅 frontmatter 信息（name/version/description/license/allowed_tools）。
    - level 2：追加 ``<body>`` 正文 markdown。
    - level 3：追加 ``<resources>`` 资源文件清单（scripts/references/assets）。
    """
    level_value = int(level)
    if level_value not in (1, 2, 3):
        raise ValueError(f"非法披露级别：{level}，仅支持 1/2/3")
    lines = [f'<skill name="{skill.name}" version="{skill.version}">']
    lines.append(f"<description>{skill.description}</description>")
    if skill.license:
        lines.append(f"<license>{skill.license}</license>")
    if skill.allowed_tools:
        lines.append(f"<allowed_tools>{', '.join(skill.allowed_tools)}</allowed_tools>")
    if level_value >= DisclosureLevel.LEVEL_2:
        lines.append("<body>")
        lines.append(skill.markdown)
        lines.append("</body>")
    if level_value >= DisclosureLevel.LEVEL_3:
        lines.append("<resources>")
        for category in ("scripts", "references", "assets"):
            files = skill.resource_files.get(category, [])
            if files:
                lines.append(f"{category}: " + ", ".join(files))
        lines.append("</resources>")
    lines.append("</skill>")
    return "\n".join(lines)


class SkillLoader:
    """SKILL.md 目录扫描与加载。

    ``load(dir)`` 完整加载一个技能包（frontmatter + 正文 + 资源分类，disclosure_level=3）；
    ``list_skills(root)`` 递归扫描目录树，识别所有 ``SKILL.md`` 并逐一加载。
    非法 frontmatter 一律抛 ``SkillError``。
    """

    def list_skills(self, root: str | Path) -> list[Skill]:
        """递归扫描 ``root`` 目录树，返回所有 SKILL.md 对应的 Skill 对象（按路径排序）。"""
        root_path = Path(root)
        if not root_path.is_dir():
            raise SkillError(f"技能根目录不存在：{root_path}")
        skills: list[Skill] = []
        for skill_md in sorted(root_path.rglob("SKILL.md")):
            rel_parts = skill_md.relative_to(root_path).parts
            if any(part.startswith(".") for part in rel_parts):
                continue
            skills.append(self.load(skill_md.parent))
        return skills

    def load(self, dir_path: str | Path) -> Skill:
        """解析单个技能包目录：frontmatter + 正文 markdown + 资源文件分类。"""
        package_dir = Path(dir_path)
        skill_md = package_dir / "SKILL.md"
        if not skill_md.is_file():
            raise SkillError(f"技能目录缺少 SKILL.md：{package_dir}")
        text = skill_md.read_text(encoding="utf-8")
        frontmatter_data, body = self._parse_skill_md(text, skill_md)
        try:
            fm = SkillFrontmatter.model_validate(_normalize_frontmatter(frontmatter_data))
        except ValidationError as exc:
            raise SkillError(f"非法 frontmatter（{skill_md}）：{exc}") from exc
        resource_files = self._classify_resources(package_dir)
        return Skill(
            name=fm.name,
            version=fm.version or "0.1.0",
            description=fm.description,
            license=fm.license,
            allowed_tools=fm.allowed_tools,
            dir=str(package_dir.resolve()),
            markdown=body,
            disclosure_level=DisclosureLevel.LEVEL_3,
            resource_files=resource_files,
        )

    @staticmethod
    def _parse_skill_md(text: str, source: Path) -> tuple[dict, str]:
        """解析 ``---`` frontmatter 块与正文；frontmatter 必须位于文件开头且闭合。"""
        if not text.startswith("---"):
            raise SkillError(f"缺少 frontmatter 块（须以 --- 开头）：{source}")
        lines = text.splitlines()
        end_index = None
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                end_index = index
                break
        if end_index is None:
            raise SkillError(f"frontmatter 块未闭合（缺少结尾 ---）：{source}")
        frontmatter_text = "\n".join(lines[1:end_index])
        body = "\n".join(lines[end_index + 1 :]).strip()
        try:
            data = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError as exc:
            raise SkillError(f"frontmatter YAML 解析失败（{source}）：{exc}") from exc
        if not isinstance(data, dict):
            raise SkillError(f"frontmatter 必须是 YAML 映射（{source}），收到：{type(data).__name__}")
        return data, body

    @staticmethod
    def _classify_resources(package_dir: Path) -> dict[str, list[str]]:
        """把 scripts/references/assets 子目录下的文件按相对路径分类。"""
        classified: dict[str, list[str]] = {}
        for category in ("scripts", "references", "assets"):
            sub_dir = package_dir / category
            if not sub_dir.is_dir():
                continue
            files = sorted(
                str(path.relative_to(package_dir)).replace("\\", "/")
                for path in sub_dir.rglob("*")
                if path.is_file()
            )
            if files:
                classified[category] = files
        return classified


class SkillRegistry:
    """技能注册表：源前缀命名空间 + name@version 去重 + compatibility 约束。

    - ``register(skill, source)``：source 形如 ``@org``（归一化为 ``@org/``），
      注册键为 ``{source}{skill.name}@{skill.version}``；同键重复注册抛 ``SkillError``。
    - ``get(name, version=None)``：name 可带 ``@org/`` 前缀；version 缺省返回最高版本。
    - ``list()``：按注册键排序返回全部已注册技能。
    """

    def __init__(self, platform_version: str = "0.1.0"):
        self.platform_version = platform_version
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill, source: str | None = "") -> str:
        """注册技能；同 name+version 重复注册或 compatibility 不满足时抛 SkillError。"""
        prefix = _normalize_source(source)
        key = f"{prefix}{skill.name}@{skill.version}"
        if key in self._skills:
            raise SkillError(f"技能已注册（name+version 去重）：{key}")
        self._check_compatibility(skill)
        self._skills[key] = skill
        return key

    def get(self, name: str, version: str | None = None) -> Skill:
        """按名称（可带 ``@org/`` 前缀）查询技能；version 缺省返回最高版本。"""
        name = name.strip()
        candidates = [
            skill
            for key, skill in self._skills.items()
            if self._split_key(key)[0] == name
            and (version is None or self._split_key(key)[1] == version)
        ]
        if not candidates:
            suffix = version or "*"
            raise SkillError(f"未注册技能：{name}@{suffix}")
        if version is not None:
            return candidates[0]
        return max(candidates, key=lambda skill: _version_key(skill.version))

    def list(self) -> list[Skill]:
        """返回全部已注册技能，按注册键排序。"""
        return [self._skills[key] for key in sorted(self._skills)]

    def _check_compatibility(self, skill: Skill) -> None:
        """compatibility 约束：精确版本或 ``>=x.y.z``（逗号分隔多值），全部不满足则报错。"""
        if not skill.compatibility:
            return
        for spec in (part.strip() for part in skill.compatibility.split(",")):
            if not spec:
                continue
            if spec.startswith(">="):
                if _version_key(self.platform_version) >= _version_key(spec[2:].strip()):
                    return
            elif spec == self.platform_version:
                return
        raise SkillError(
            f"技能 {skill.name} 兼容性 {skill.compatibility!r} 不满足平台版本 {self.platform_version!r}"
        )

    @staticmethod
    def _split_key(key: str) -> tuple[str, str]:
        """把注册键拆成（限定名, 版本）。"""
        qualified, _, version = key.rpartition("@")
        return qualified, version


class SkillCatalog:
    """按角色挂载的技能目录。

    ``mount(role, skills)`` 只挂载 ``Role.skills`` 中以 ``name@version`` 指定的技能；
    ``allowed_tools(role)`` 返回技能 allowed_tools 与角色 tools 的交集
    （技能 allowed_tools 为 None 表示不限制，放行全部角色工具）。
    """

    def __init__(self) -> None:
        self._mounted: dict[str, list[Skill]] = {}

    def mount(self, role: Role, skills: Iterable[Skill]) -> list[Skill]:
        """挂载角色技能清单中出现的技能，返回实际挂载列表并缓存到目录。"""
        wanted = set(role.skills)
        mounted = [skill for skill in skills if f"{skill.name}@{skill.version}" in wanted]
        self._mounted[role.id] = mounted
        return mounted

    def mounted_skills(self, role: Role) -> list[Skill]:
        """返回该角色已挂载的技能列表（未挂载返回空列表）。"""
        return list(self._mounted.get(role.id, []))

    def allowed_tools(self, role: Role) -> list[str]:
        """返回技能 allowed_tools 与角色 tools 的交集（按名称排序）。"""
        allowed: set[str] = set()
        for skill in self._mounted.get(role.id, []):
            if skill.allowed_tools is None:
                allowed.update(role.tools)
            else:
                allowed.update(set(skill.allowed_tools) & set(role.tools))
        return sorted(allowed)
