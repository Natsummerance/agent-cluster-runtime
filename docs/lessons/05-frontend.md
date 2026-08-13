# 05-frontend —— 前端 / i18n / Playwright

> 何时加载：前端开发、前端测试、i18n、Playwright 定位时**必读**。

## 数据红线
- 前端**严禁伪造/硬编码数据**，全面接真实后端。
- `frontend/e2e/mock-api.ts` 仅 Playwright 拦截式测试合法（`npm run e2e` 用），`e2e-real` 一律真实后端。

## i18n
- key 命名 `<page>.<component>.<token>`；`errors.*` snake_case 对齐后端 code。
- 测试用 `frontend/src/test/renderWithIntl.tsx`；`npm run i18n:check` 校验 key 对齐。

## Playwright 定位
- AntD 下拉**没有 role=option** → 用 `.ant-select-item-option` + `hasText`。
- e2e-real（`frontend/e2e-real/`）：确定性套件 16 条，打真实 `uv run agent-cluster serve`。
- 本地验证流程：`Start-Process uv ... serve --port 8765 --auth-token ci`（Hidden）→ 探活
  （带 `X-Auth-Token: ci` 头，90s 窗口）→ `cd frontend; npm run e2e:real` → `taskkill /T /F` 杀进程树。

## 测试成本
- vitest 全量 140s+：只改少量文件时 `npx vitest run <file1> <file2> ...`。
- 版本相关 mock 在 `frontend/src/test/{api-client,appStore,i18n,Settings}.test.*`。