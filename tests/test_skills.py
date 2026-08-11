"""Task 2 技能层行为测试。

覆盖：示例技能解析（frontmatter/正文/资源分类）、缺 name 报错、name+version
去重与兼容性约束、@org/name 源前缀、按角色挂载交集、三级渐进披露内容差异。
"""

from pathlib import Path

import pytest

from agent_cluster.models import Role, RoleKind, Skill
from agent_cluster.skills import (
    DisclosureLevel,
    SkillCatalog,
    SkillError,
    SkillFrontmatter,
    SkillLoader,
    SkillRegistry,
    format_skill_context,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_SKILLS = REPO_ROOT / "examples" / "skills"


def make_role(role_id: str, skills: list[str], tools: list[str]) -> Role:
    """构造最小可用的 Role 对象。"""
    return Role(
        id=role_id,
        name=role_id,
        kind=RoleKind.PM,
        goal="测试岗位",
        backstory="测试岗位背景",
        skills=skills,
        tools=tools,
    )


def make_skill(name: str, version: str, compatibility: str | None = None) -> Skill:
    return Skill(
        name=name,
        version=version,
        description=f"{name} 描述",
        compatibility=compatibility,
        allowed_tools=["read_file"],
    )


# ---------------------------------------------------------------------------
# 示例技能解析
# ---------------------------------------------------------------------------


def test_list_skills_loads_at_least_two_example_skills():
    loader = SkillLoader()
    skills = loader.list_skills(EXAMPLES_SKILLS)
    assert len(skills) >= 2
    names = {skill.name for skill in skills}
    assert {"requirement-analysis", "backend-api-design"} <= names


def test_load_example_skill_parses_frontmatter_and_resources():
    loader = SkillLoader()
    skill = loader.load(EXAMPLES_SKILLS / "requirement-analysis")
    assert skill.name == "requirement-analysis"
    assert skill.version == "1.0.0"
    assert "需求分析" in skill.description
    assert skill.license == "MIT"
    assert skill.allowed_tools == ["read_file", "write_file"]
    assert skill.markdown.startswith("# 需求分析执行指引")
    assert skill.dir.endswith("requirement-analysis")
    assert skill.disclosure_level == DisclosureLevel.LEVEL_3
    assert skill.resource_files["scripts"] == ["scripts/checklist.py"]
    assert skill.resource_files["references"] == ["references/prd-template.md"]
    assert skill.resource_files["assets"] == ["assets/example-prd.txt"]


def test_load_normalizes_kebab_case_and_metadata_version(tmp_path: Path):
    package = tmp_path / "sample-skill"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\n"
        "name: sample-skill\n"
        "description: 示例技能\n"
        "allowed-tools:\n"
        "  - read_file\n"
        "metadata:\n"
        "  version: 3.2.1\n"
        "---\n正文",
        encoding="utf-8",
    )
    skill = SkillLoader().load(package)
    assert skill.allowed_tools == ["read_file"]
    assert skill.version == "3.2.1"


def test_skill_frontmatter_required_fields():
    fm = SkillFrontmatter(name="sample-skill", description="示例")
    assert fm.version is None
    assert fm.allowed_tools is None
    assert fm.compatibility is None


def test_load_skill_without_version_defaults_to_0_1_0(tmp_path: Path):
    package = tmp_path / "no-version"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\nname: no-version\ndescription: 无版本技能\n---\n正文", encoding="utf-8"
    )
    skill = SkillLoader().load(package)
    assert skill.version == "0.1.0"


# ---------------------------------------------------------------------------
# 非法 frontmatter
# ---------------------------------------------------------------------------


def test_load_missing_name_raises_skill_error(tmp_path: Path):
    package = tmp_path / "no-name"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\ndescription: 缺少 name\n---\n正文", encoding="utf-8"
    )
    with pytest.raises(SkillError, match="name"):
        SkillLoader().load(package)


def test_load_missing_frontmatter_raises_skill_error(tmp_path: Path):
    package = tmp_path / "no-frontmatter"
    package.mkdir()
    (package / "SKILL.md").write_text("没有 frontmatter 的正文", encoding="utf-8")
    with pytest.raises(SkillError, match="frontmatter"):
        SkillLoader().load(package)


def test_load_invalid_yaml_raises_skill_error(tmp_path: Path):
    package = tmp_path / "bad-yaml"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\nname: [unclosed\n---\n正文", encoding="utf-8"
    )
    with pytest.raises(SkillError):
        SkillLoader().load(package)


# ---------------------------------------------------------------------------
# 注册表：去重 / 源前缀 / 版本 / 兼容性
# ---------------------------------------------------------------------------


def test_register_dedupe_by_name_and_version():
    registry = SkillRegistry()
    registry.register(make_skill("req-analysis", "1.0.0"))
    with pytest.raises(SkillError, match="去重"):
        registry.register(make_skill("req-analysis", "1.0.0"))
    registry.register(make_skill("req-analysis", "1.1.0"))
    assert len(registry.list()) == 2


def test_register_source_prefix_and_get():
    registry = SkillRegistry()
    registry.register(make_skill("req-analysis", "1.0.0"), source="@acme")
    skill = registry.get("@acme/req-analysis")
    assert skill.name == "req-analysis"
    with pytest.raises(SkillError):
        registry.get("req-analysis")
    assert [s.name for s in registry.list()] == ["req-analysis"]


def test_get_without_version_returns_highest():
    registry = SkillRegistry()
    registry.register(make_skill("req-analysis", "1.0.0"))
    registry.register(make_skill("req-analysis", "2.3.0"))
    assert registry.get("req-analysis").version == "2.3.0"
    assert registry.get("req-analysis", version="1.0.0").version == "1.0.0"
    with pytest.raises(SkillError):
        registry.get("req-analysis", version="9.9.9")


def test_register_enforces_compatibility_constraint():
    registry = SkillRegistry(platform_version="0.1.0")
    with pytest.raises(SkillError, match="兼容性"):
        registry.register(make_skill("too-new", "1.0.0", compatibility=">=9.9.9"))
    registry.register(make_skill("exact-ok", "1.0.0", compatibility="0.1.0"))
    registry.register(make_skill("range-ok", "1.0.0", compatibility=">=0.1.0, <=1.0.0"))
    registry.register(make_skill("unconstrained", "1.0.0"))
    assert len(registry.list()) == 3


def test_register_rejects_invalid_source_prefix():
    registry = SkillRegistry()
    with pytest.raises(SkillError, match="@"):
        registry.register(make_skill("req-analysis", "1.0.0"), source="acme")


# ---------------------------------------------------------------------------
# 按角色挂载与工具交集
# ---------------------------------------------------------------------------


def test_mount_only_skills_listed_in_role():
    loader = SkillLoader()
    skills = loader.list_skills(EXAMPLES_SKILLS)
    role = make_role(
        role_id="pm",
        skills=["requirement-analysis@1.0.0"],
        tools=["read_file", "write_file"],
    )
    catalog = SkillCatalog()
    mounted = catalog.mount(role, skills)
    assert [skill.name for skill in mounted] == ["requirement-analysis"]
    assert [skill.name for skill in catalog.mounted_skills(role)] == ["requirement-analysis"]


def test_allowed_tools_intersection_with_role_tools():
    loader = SkillLoader()
    skills = loader.list_skills(EXAMPLES_SKILLS)
    role = make_role(
        role_id="backend",
        skills=["backend-api-design@2.1.0"],
        tools=["read_file", "bash", "search"],
    )
    catalog = SkillCatalog()
    catalog.mount(role, skills)
    # backend-api-design allowed_tools=[read_file, write_file, bash] ∩ role tools
    assert catalog.allowed_tools(role) == ["bash", "read_file"]


def test_allowed_tools_unrestricted_skill_passes_all_role_tools():
    unrestricted = make_skill("unrestricted", "1.0.0")
    unrestricted.allowed_tools = None
    role = make_role(
        role_id="pm",
        skills=["unrestricted@1.0.0"],
        tools=["read_file", "bash"],
    )
    catalog = SkillCatalog()
    catalog.mount(role, [unrestricted])
    assert catalog.allowed_tools(role) == ["bash", "read_file"]


# ---------------------------------------------------------------------------
# 三级渐进披露
# ---------------------------------------------------------------------------


def test_format_skill_context_three_levels_increase_content():
    loader = SkillLoader()
    skill = loader.load(EXAMPLES_SKILLS / "requirement-analysis")
    level1 = format_skill_context(skill, DisclosureLevel.LEVEL_1)
    level2 = format_skill_context(skill, DisclosureLevel.LEVEL_2)
    level3 = format_skill_context(skill, DisclosureLevel.LEVEL_3)

    assert level1.startswith('<skill name="requirement-analysis"')
    assert level1.count("</skill>") == 1
    assert "<description>" in level1
    assert "<body>" not in level1
    assert "<resources>" not in level1

    assert "<body>" in level2
    assert "需求分析执行指引" in level2
    assert "<resources>" not in level2
    # level 2 保留 level 1 的 frontmatter 区块
    assert '<skill name="requirement-analysis"' in level2
    assert "<description>" in level2
    assert "<license>" in level2
    assert "<allowed_tools>" in level2

    assert "<resources>" in level3
    assert "scripts/checklist.py" in level3
    assert "references/prd-template.md" in level3
    assert "assets/example-prd.txt" in level3
    # level 3 保留 level 2 的正文区块
    assert "<body>" in level3
    assert "需求分析执行指引" in level3


def test_format_skill_context_accepts_int_level():
    loader = SkillLoader()
    skill = loader.load(EXAMPLES_SKILLS / "backend-api-design")
    assert "<body>" in format_skill_context(skill, 2)


def test_format_skill_context_rejects_invalid_level():
    loader = SkillLoader()
    skill = loader.load(EXAMPLES_SKILLS / "backend-api-design")
    with pytest.raises(ValueError):
        format_skill_context(skill, 4)
