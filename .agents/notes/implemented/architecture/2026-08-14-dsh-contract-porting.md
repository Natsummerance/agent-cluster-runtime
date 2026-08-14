# dsh 契约级移植基线（v0.7）

- 日期：2026-08-14
- 类：architecture
- 状态：implemented

## 决策
v0.7.0 以 DeepSeek Harness（commit `47f943859b` / `0.1.0-rc.5`，MIT）为范本做
**契约级移植**：事件词汇、JSONL 头格式、配置分层语义、spill/credentials/guard、
生成器+校验器、Agent Notes、postmortem 照搬契约，Python/React 等价实现；
前端会话/轨迹视图源码级适配。完整清单见 [docs/porting/2026-08-14-dsh-porting.md](../../docs/porting/2026-08-14-dsh-porting.md)。

## 否决项
不搬运 Cordis 本体（TS effect 追踪无法在 Python 复用，以 `seam.py` 等价）、
Typert、landlock-run、遥测匿名头（隐私缺陷）、逐文件 100% 行覆盖门禁。

## 已落地（T14.1–14.8）
- `events.py`（事件日志核心 + model-visible ⟺ logged 不变量）
- `session_log_store.py`（JSONL/SQLite 双后端 + v0.6 自动迁移）+ `spill.py`
- `seam.py`（能力接缝三角 + effect 作用域）+ `credentials.py` + `guard.py`
- `cache.py` / `context.py`（LLM 缓存统计 + 头锚定裁剪，≥98% 稳态门槛实测通过）
- `config_layers.py`（profile/bundle/patch + `dump-config` CLI）
- `.agents/notes/` / `docs/postmortem/` / `gen_*`+`verify_*` 生成器 / pre-push 技能
