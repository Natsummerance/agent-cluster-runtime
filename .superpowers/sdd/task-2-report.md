# Task 2 报告：技能层（SKILL.md 加载与渐进披露）

- 状态：完成（无阻塞）
- 提交：`9b8e68c`（`Task 2: 技能层 SkillLoader/Registry`，基于 `c4f65a9` Task 1 ledger）
- 工作目录：`T:\Programming\Project\codex\agent\agent-cluster-runtime`（未触碰参考目录 `agent-clusters`；模板 `anthropic-skills/template/SKILL.md` 仅读格式参考，未复制内容）

## 1. 实现内容

### `src/agent_cluster/skills.py`（新增，公开导出 7 个名字）
- `SkillError`：技能层统一异常（解析失败 / 注册冲突 / 兼容性不满足 / 未找到等）。
- `DisclosureLevel`（`IntEnum`）：`LEVEL_1=1`（仅 frontmatter）/ `LEVEL_2=2`（加载正文）/ `LEVEL_3=3`（登记资源文件），对齐 §5.5。
- `SkillFrontmatter`（pydantic）：`name`（小写连字符，正则校验）+ `description`（≤1024）必填；可选 `license`/`compatibility`/`version`/`allowed_tools`。加载时把 `allowed-tools`（kebab-case）与 `metadata.version`（嵌套）归一化到本模型字段。
- `format_skill_context(skill, level)`：输出 `<skill name="..." version="...">` 稳定锚块；level 1 仅 frontmatter（description/license/allowed_tools），level 2 追加 `<body>` 正文 markdown，level 3 追加 `<resources>` 资源文件清单（scripts/references/assets）。接受 `int` 或 `DisclosureLevel`，非法级别抛 `ValueError`。
- `SkillLoader`：
  - `list_skills(root)`：递归扫描目录树识别 `SKILL.md`（跳过 `.` 开头隐藏目录），按路径排序返回 `list[Skill]`。
  - `load(dir)`：解析 `---` frontmatter 块（PyYAML `safe_load`）+ 正文；资源文件按 scripts/references/assets 子目录递归分类为相对路径清单；完整加载并置 `disclosure_level=3`；缺 name / 缺 frontmatter / YAML 非法 / frontmatter 非映射均抛 `SkillError`。
- `SkillRegistry`：`register(skill, source)`（source 形如 `@org`，归一化为 `@org/` 前缀，注册键 `@org/name@version`）；同 `name+version` 重复注册抛 `SkillError`；`compatibility` 平台版本约束（精确版本或 `>=x.y.z`，逗号分隔多值，全部不满足则拒绝）；`get(name, version=None)`（可带 `@org/` 前缀，version 缺省返回最高版本）；`list()`（按注册键排序）。
- `SkillCatalog`：`mount(role, skills)` 只挂载 `Role.skills` 中 `name@version` 指定的技能并缓存；`mounted_skills(role)` 返回已挂载列表；`allowed_tools(role)` 返回技能 `allowed_tools` 与角色 `tools` 的交集（技能 `allowed_tools=None` 视为不限制，放行全部角色工具）。

### `src/agent_cluster/models.py`（小改）
- `Skill` 模型新增 `compatibility: str | None = None` 字段（Task 1 模型缺该字段，而本任务要求注册表 `compatibility` 约束且 frontmatter 含 `compatibility`，属必要增补；默认 `None`，向后兼容）。复用 Task 1 的 `Skill` 模型，未在 skills.py 重定义。

### 示例技能包 `examples/skills/`（2 个，frontmatter 均含 name/description/version/license/allowed-tools）
- `requirement-analysis/`：`SKILL.md` + `scripts/checklist.py` + `references/prd-template.md` + `assets/example-prd.txt`。
- `backend-api-design/`：`SKILL.md` + `references/api-contract.md` + `assets/curl-example.txt`。

### `src/agent_cluster/__init__.py`
- 新增导出：`SkillError`、`DisclosureLevel`、`SkillFrontmatter`、`format_skill_context`、`SkillLoader`、`SkillRegistry`、`SkillCatalog`（加入 `__all__`）。

## 2. 测试与验证

### 测试文件
- `tests/test_skills.py`：19 个用例，覆盖：
  - 示例技能解析：`list_skills` 加载 ≥2 技能、frontmatter/正文/资源分类字段、kebab-case `allowed-tools` 与 `metadata.version` 归一化、无 version 回退 `0.1.0`；
  - 非法 frontmatter：缺 name、缺 frontmatter 块、YAML 非法均抛 `SkillError`；
  - 注册表：`name+version` 去重报错、`@org/name` 源前缀（`get("@acme/req-analysis")` 命中、裸名不命中）、`get` 无版本返回最高版本、`compatibility` 约束（`>=9.9.9` 拒绝 / 精确与范围通过 / 未声明放行）、非法源前缀报错；
  - 角色挂载：只挂载 `Role.skills` 指定的 `name@version`、`allowed_tools` 交集（含 `allowed_tools=None` 不限制场景）；
  - 渐进披露：三级内容逐级增加（level 1 无 body/resources → level 2 含 body → level 3 含资源清单）、接受 int 级别、非法级别抛 `ValueError`。

### 执行命令与输出（均在 `agent-cluster-runtime` 目录）
- `uv run pytest -q` → 首次 `6 failed, 45 passed`（原因见偏差 1/2），修正后 → `52 passed in 0.24s`（含 Task 1 的 33 个用例，全绿）。
- 冒烟验证：`uv run python -` 从 `examples/skills` 加载出 2 个技能（`backend-api-design@2.1.0`、`requirement-analysis@1.0.0`），三级 `format_skill_context` 输出逐级增加，注册表 `@demo/` 前缀注册/查询、按角色挂载与工具交集均符合预期（输出见上方对话记录）。
- `uv run python -m agent_cluster` → 正常打印版本与用法占位。

## 3. 设计决策与偏差说明（简报范围内的合理决策，已写入 docstring）

1. `Skill` 模型新增 `compatibility` 字段：Task 1 交付的模型无此字段，但简报要求 `SkillFrontmatter` 含 compatibility 且注册表执行 `compatibility` 约束；不重定义 `Skill` 的前提下在 models.py 增补该字段（默认 `None`），属最小必要变更。
2. frontmatter 归一化（`allowed-tools` → `allowed_tools`、`metadata.version` → `version`）放在 `SkillLoader` 层完成，`SkillFrontmatter` 字段名保持与简报一致；直接对模型传 kebab-case 键不会被识别（`extra="ignore"`），已通过 loader 级测试覆盖。
3. `compatibility` 约束语义：注册表以 `platform_version`（默认 `0.1.0`）比对，支持精确版本或 `>=x.y.z`（逗号分隔多值），全部不满足则 `SkillError`；未声明 compatibility 的技能不受限。
4. `SkillLoader.load` 始终完整加载（`disclosure_level=3`，frontmatter + 正文 + 资源分类）；三级披露的“差异”由 `format_skill_context` 按需输出实现，符合 §5.5“仅在触发时提升披露级别”的意图。简报未要求按级别惰性加载（mtime 缓存/并发加载属设计文档建议，YAGNI 未实现）。
5. `SkillRegistry.get(name, version=None)` 无版本时返回最高 semver 版本；未找到抛 `SkillError`（非返回 None）。
6. `SkillCatalog.mount` 按 `name@version` 精确匹配 `Role.skills`；`allowed_tools` 对多技能取并集（任一挂载技能允许且角色拥有即放行），`allowed_tools=None` 表示该技能不限制工具。
7. 资源文件清单使用正斜杠相对路径（跨平台稳定），示例技能均已覆盖三类子目录分类。

## 4. 注意事项（供后续任务）
- `examples/skills` 现有 `requirement-analysis` 与 `backend-api-design` 两个技能包；Task 7 将在此基础上补齐 `frontend-design`、`qa-testing` 至 4 个岗位技能包。
- 技能目录默认按 `skills/<name>/SKILL.md` 组织，`list_skills` 递归扫描任意深度，并跳过隐藏目录（`.git`/`.venv` 等）。
- `ClusterState.skill_catalog`（`dict[str, Skill]`）与 `SkillCatalog`（按角色缓存挂载）是两种用途：前者是 LangGraph 共享 channel，后者是运行时按角色查询视图，后续任务可直接配合使用。
- PowerShell 7 `Set-Content -Encoding UTF8` 写入无 BOM UTF-8；Git 提示 LF→CRLF 为仓库默认 autocrlf 行为，无影响。
