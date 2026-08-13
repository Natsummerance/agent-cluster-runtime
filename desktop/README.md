# AgentClusterWorkbench —— Electron 桌面壳（v0.6.0）

把 Python 后端（`agent-cluster serve`）与 React 前端工作台（`frontend/`）串成桌面应用的 Electron 壳。

## 架构

```
┌────────────────────────────── Electron 主进程（desktop/main.js） ─────────────────────────────┐
│  spawn（随包 agent-cluster-backend.exe 或 uv run agent-cluster serve，cwd=仓库根）               │
│       │ --port <随机空闲端口> [AGENT_CLUSTER_SERVE_ARGS 附加参数]                                 │
│       ▼                                                                                         │
│  Python 后端  asyncio 单进程 + stdlib ThreadingHTTPServer（REST + SSE）                          │
│       ▲ 轮询 http://127.0.0.1:<port>/api/v1/status（就绪 + 等待审批通知）                         │
│       └──────────┬───────────────────────────────────────────────┐                              │
│  BrowserWindow（preload.js 注入 window.agentCluster.getBackendUrl）│   Tray / 全局快捷键 / 通知   │
│       ▼                                                           │                              │
│  React 工作台：开发 http://127.0.0.1:5173（frontend/），生产 dist-frontend/index.html            │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- 主进程负责后端进程生命周期、空闲端口选取、就绪探测、托盘/通知/快捷键、开机自启与退出清理。
- 渲染进程（React）通过 `preload.js` 暴露的 `window.agentCluster.getBackendUrl()` 拿到后端地址，REST/SSE 全部由前端直接请求。
- 后端默认仅监听 `127.0.0.1`；通过 `AGENT_CLUSTER_SERVE_ARGS` 可附加参数（如 `--auth-token <token>`），此时壳的轮询会自动带上 `X-Auth-Token` 头。

## 开发运行

前置：仓库根已 `uv sync`（或 uv 可用）、前端开发服务器已启动（`cd frontend && npm run dev`，默认 5173）。

```powershell
cd desktop
npm install
npm start
```

- 开发模式加载 `AGENT_CLUSTER_FRONTEND_URL`（默认 `http://127.0.0.1:5173`）。
- 启动时若 `desktop/resources/agent-cluster-backend.exe` 存在则直接使用，否则回退 `uv run agent-cluster serve`（cwd=仓库根）。

## 打包

```powershell
cd desktop
npm run build:win    # electron-builder --win nsis，产物在 desktop/dist/
npm run pack         # electron-builder --dir，仅生成未打包目录
```

`electron-builder.yml` 约定：

- `appId`：`com.natsummerance.agent-cluster`；`productName`：`AgentClusterWorkbench`。
- `files`：`main.js`、`preload.js`、`package.json`、`dist-frontend/**`、`resources/**`。
- NSIS：`oneClick: false`（向导式安装，可选安装目录）。

## 资源路径约定

| 资源 | 路径 |
| --- | --- |
| 前端生产构建 | `desktop/dist-frontend/index.html`（打包前由 frontend 构建产出） |
| 随包后端可执行文件 | 打包后 `process.resourcesPath/agent-cluster-backend.exe`；开发时 `desktop/resources/agent-cluster-backend.exe` |
| 安装包 / 未打包产物 | `desktop/dist/` |

全部路径使用 `path.join` 拼装，避免平台分隔符问题。

## 环境变量

| 变量 | 作用 |
| --- | --- |
| `AGENT_CLUSTER_FRONTEND_URL` | 开发模式前端地址（默认 `http://127.0.0.1:5173`） |
| `AGENT_CLUSTER_SERVE_ARGS` | 附加给 `serve` 的参数，如 `--auth-token secret` |
| `AGENT_CLUSTER_AUTOSTART` | 设为 `1` 启用登录自启（`app.setLoginItemSettings`） |

## 退出清理

退出时（`will-quit`）会先 `child.kill()` 停止后端；Windows 下随后执行 `taskkill /pid <pid> /T /F` 清理 `uv`/后端进程树，避免残留进程。