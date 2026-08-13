# 02-testing —— pytest / 测试设计

> 何时加载：写/改测试、跑 pytest、QA 偶发失败、超时类失败时**必读**。

## 陈旧 pyc 竞态（QA 偶发 `assert -1 == 3` 假失败，烧了最多 token 的坑之一）
- 现象：场景 B 基线初跑后，`edit_file` 同秒同长改写文件 → 嵌套 pytest 命中陈旧字节码。
- 根因：pytest 按整秒校验 pyc 时间戳。
- 预防：基线初跑后 `shutil.rmtree` 清理 `__pycache__` / `.pytest_cache`（`tests/test_acceptance_v04.py` 场景 B）。
- 诊断：重跑一次看是否必现；检查 `__pycache__` 时间戳与源文件。

## 超时设计
- **测试 HTTP 超时必须 > 被测子进程内部超时之和**：`/api/v1/doctor` 的 `docker info` 内部 15s，
  测试 `urlopen(timeout=8)` 偶发 TimeoutError → 已改 30s（`tests/test_doctor.py`）。
- 后端起服/恢复类断言给足终态等待（30-60s），并带状态/error 诊断输出。

## 跨平台
- 禁 `cmd /c echo` → 用 `python -c "print(...)"`（`tests/test_tools.py`）。
- 平台相关断言要隔离：`os.name` patch 抽 helper；junction/权限断言非 Windows 跳过。

## 成本与时机
- 全量 `uv run pytest -q`：650 passed，约 2-3 分钟 → **只留最终验收跑**。
- 版本类改动：跑 `tests/test_t12_11.py`（版本一致性断言）+ `tests/test_t12_12.py` 即可。
- 前端 vitest 全量 140s+ → 改动只影响少数文件时用 `npx vitest run <files>`。