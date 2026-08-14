# pre-push-checks —— 按 change-scope 选择最小测试集

> v0.7 T14.8 移植自 dsh `dsh-pre-push-checks`：绝不盲目跑全仓。

## 用法
提交/推送前，按本次 change-scope 选择最小测试集：

| 改动范围 | 最小测试集 |
|---|---|
| 仅 docs/、scripts/ 生成器 | `uv run pytest scripts 相关 verify` 或跳过 |
| 新后端模块（events/seam/cache/context/…） | 对应 `tests/test_t14_N.py` |
| 改 runtime/session/server 核心 | 对应模块测试 + 全量 `uv run pytest -q` |
| 改前端 | `cd frontend; npx tsc --noEmit; npx vitest run; npm run build` |
| 版本/发布 | `uv run pytest tests/test_t12_11.py tests/test_t12_12.py -q` + 全量 |

## 原则
- 快检查点本地跑（≤2 分钟），穷尽覆盖归 CI。
- 改动核心文件（runtime.py/session.py/server.py）时最小集必须包含该文件相关测试。
- 任何修复前先读 `docs/lessons/07-debugging.md`（3 次即停）。
