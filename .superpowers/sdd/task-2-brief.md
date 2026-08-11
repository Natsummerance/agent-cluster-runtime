## Task 2: 技能层（SKILL.md 加载与渐进披露）

- 目标：实现 §5.5 技能注册与加载：目录扫描、frontmatter 解析、版本/兼容、按角色挂载、三级渐进披露。
- 产出：
  - `src/agent_cluster/skills.py`：
    - `SkillFrontmatter`（pydantic：name/description 必填，license/compatibility/allowed_tools/version 可选）。
    - `SkillLoader`：`list_skills(root) -> list[Skill]` 递归扫描目录树，识别 `SKILL.md`；`load(dir) -> Skill` 解析 frontmatter（用 `PyYAML` safe_load 解析 `---` 块）+ 正文 markdown；资源文件按 `scripts/references/assets` 子目录分类；非法 frontmatter 抛 `SkillError`。
    - `SkillRegistry`：`register(skill, source)`、`get(name, version=None)`、`list()`；支持 `@org/name` 源前缀；`name+version` 去重（同版本覆盖报错或按规则告警）；`compatibility` 约束。
    - `SkillCatalog`：按角色挂载——`mount(role, skills)` 只挂载 `Role.skills` 指定的 `name@version`；`allowed_tools(role)` 返回 技能 allowed_tools ∩ 角色 tools。
    - 渐进披露：`DisclosureLevel`（1=仅 frontmatter 建目录，2=加载正文，3=登记资源文件）；`format_skill_context(skill, level)` 输出 `<skill name="...">` 锚块，level 2/3 追加正文与资源清单。
  - `examples/skills/`：至少 2 个示例技能包（如 `requirement-analysis/SKILL.md`、`backend-api-design/SKILL.md`），frontmatter 含 name/description/version/allowed_tools。
  - `tests/test_skills.py`：解析示例技能、缺 name 报错、版本去重、按角色挂载交集、三级披露内容差异。
- 验收：测试全绿；可从 `examples/skills` 加载出 ≥2 个技能；`format_skill_context` 三级输出逐级增加内容。


