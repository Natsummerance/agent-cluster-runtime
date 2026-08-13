# 06-versioning —— 版本升级 / 发布同步

> 何时加载：任何版本号变更（含维护版）时**必读**。

## 铁律：精确字符串替换
- **禁止全文替换** `0.6.x`：`0.6.0` 是 `10.6.0` / `>=0.6.0` / `^0.6.0` 的子串，
  曾把 desktop lock 的依赖 engines/source-map 版本误改（v0.6.1 实测踩中，多花一轮全量 diff 审查）。
- 用带引号/带上下文的精确串：`"version": "0.6.1"`、`__version__ = "0.6.1"`、`'0.6.1'`（前端 mock）。
- 替换后 `git diff` 逐行审查 +/- 行，确认无依赖版本被误伤。

## 同步清单（一处不漏）
1. `pyproject.toml`（`version = "..."`）
2. `src/agent_cluster/__init__.py`（`__version__`）
3. `src/agent_cluster/server.py`（`-dev` 回退，2 处）
4. `src/agent_cluster/mcp_client.py`（clientInfo）
5. `frontend/package.json`（version + description）
6. `frontend/package-lock.json`（顶层 + `packages[""]`，精确串）
7. `desktop/package.json`
8. `desktop/package-lock.json`（同 6）
9. 前端 mock：`frontend/src/test/{api-client,appStore,i18n,Settings}.test.*`
10. `tests/test_t12_11.py`（docstring + 2 断言）、`tests/test_t12_12.py`（docstring + 断言）
11. `README.md` / `docs/MANUAL.md` / `docs/PRODUCT.md` 顶部版本行（PRODUCT 历史特性标注如「已实现（0.6.0）」**不动**）
12. `uv.lock`：由 `uv run python` 自动更新（agent-cluster 条目）

## 验证命令
- `uv run pytest tests/test_t12_11.py tests/test_t12_12.py -q`（版本一致性断言）。
- `uv run python -c "import json,tomllib; ..."` 校验四文件一致。
- 前端：`npx vitest run` 四个 mock 文件。