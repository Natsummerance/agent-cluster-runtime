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
## CORS（浏览器/桌面直连 serve）
- 浏览器 dev（5173）或桌面版（file://）fetch `http://127.0.0.1:8765` 会触发 CORS 预检；
  后端必须返回 `Access-Control-Allow-Origin: *` 并实现 `do_OPTIONS`（允许 X-Auth-Token/Content-Type），
  SSE 响应同样要带 ACAO 头。
- 本地跑 dev：`.venv\Scripts\python.exe -m agent_cluster.cli serve` + `npm run dev`；
  无头验证 = 页面 `fetch('http://127.0.0.1:8765/api/v1/status')` + 收集 console 错误。
- Playwright 探活**不要用 networkidle**：SSE 长连接永不 idle，用 `domcontentloaded` + 元素等待。
## API 信封契约（高频坑）
- 后端信封是 `{ok, data}`，**data 常为对象**（`data.proposals` / `data.plugins` / `data.skills` / `data.mcp`），
  前端端点函数必须解包后返回数组；mock 测试要用真实结构（`{ok:true,data:{proposals:[...]}}`），
  用 `data: []` 会掩盖契约不匹配，antd Table 收到对象即崩（`rawData.some is not a function`）。
- 旧项目（v0.6 T13.5 双写前）只在全局索引、无 ProjectStore 记录：`/projects/{id}/dashboard` 等
  端点会 KeyError→404；列表接口有兜底但独立端点没有。修复约定：索引在 → 返回空三轴 200，索引无 → 404。
- 验证页面：无头浏览器逐页收集 console/pageerror + `response 404` 监听，比肉眼快。