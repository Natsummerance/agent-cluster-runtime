'use strict';

// AgentClusterWorkbench —— Electron 桌面壳（v0.6.1）
// 职责：启动/托管 agent-cluster serve 后端（REST+SSE），加载 React 前端工作台，
// 提供托盘、系统通知（等待审批）、全局快捷键、开机自启与退出清理。
//
// 架构：main.js（Electron 主进程）──spawn──▶ agent-cluster serve（asyncio + ThreadingHTTPServer）
//                     │ http://127.0.0.1:<port>/api/v1/*
//                     └──BrowserWindow──preload──▶ React 工作台（frontend/ 或 dist-frontend/）

const { app, BrowserWindow, Tray, Menu, Notification, globalShortcut, nativeImage, dialog } = require('electron');
const { spawn, execFileSync } = require('child_process');
const net = require('net');
const http = require('http');
const { URL } = require('url');
const path = require('path');
const fs = require('fs');
const updater = require('./updater.js');

const SMOKE_MODE = process.argv.includes('--smoke');
const REPO_ROOT = path.resolve(__dirname, '..');
const BACKEND_READY_TIMEOUT_MS = 30000;
const STATUS_POLL_INTERVAL_MS = 10000;
const NOTIFICATION_MIN_INTERVAL_MS = 45000;
const TRAY_TOOLTIP = 'AgentClusterWorkbench';

// 内嵌 32x32 PNG 托盘图标（运行时生成，避免额外资源文件依赖）。
const TRAY_ICON_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAACYElEQVR4nO2X6U4TURTHJ+EFeAQTAgargKK4sUhL+wJ9EL+bGDGIKwioSLEL/cSLlC6uWHBBBSVoTPjSN2iu+d3MIZMJzNzO2ExIOMlJ7tyz/O//nNM7U8s6kWMlqWKpI7myFkuurKUmCuU0ypo9bO3CJf9EoXwzka+sxnPVejxXbYxna02UNXvY8MH3f+GmiqVOcsZz1dJ4tqZuvHqtxpbfaB3NvNUqz9jwwZcYYsNyTuQrGTiSG6yRpXdq+OV7rdcXP2iVZ2z42OdoEhu0Fjb2KnzgJrjXXqyrq88/ar3yrK5VnrHJOYghlhytnoG62bwPsOEJxuWFDTU0v6m8BF/XGTKt9ILeUT/Bhhc8wb0090kNzn7xxMeXGMcZmuQ0rTvzQw+Ft2BffPpZXZj56omN4EuM1IFc5DTpg81dzxG9pOZO7IHH33zx8SWGWHKQi5x+NeD+YF6EOzWk39TcFBvBlxhipQ/kJLfXHWXXvk7PnNzp9/knW6rv4Q9fbHzwJcZZA3KS26sH3KPcZYfhm3I/92Bb+x6B3wDDo/dpZpV+ydxJ7VsV6YHMoT0D/A7SQfD7H31XZ+/vqN6pXdUz+Vt13f6rTt3a18qaPWz44BsE36/+1PbMvV/q9N091X3nj8ZFWbOHLUz9TeYvNv1T8wQPzihr9rCFmT+T3x/8wIErmChr9oR70N+fZXj/wBEseo2yZk+wg94/luH9S33BYs5Q1uw5sYPev5bh+wcFD5VnbIId9P1jGb5/5SyCiWLDJ+z714r4+8N1hki+v0Si/P501yKK72+3RPX/40TaI/8ATtOdWeIReB8AAAAASUVORK5CYII=';

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------
let backendProcess = null;
let backendUrl = null;
let backendAuthToken = '';
let mainWindow = null;
let tray = null;
let isQuitting = false;
let lastNotificationAt = 0;
let notifiedSessionIds = new Set();

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
function log(...args) {
  console.log('[desktop]', ...args);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 动态选取空闲端口：临时 listen 0 拿到端口后关闭。 */
function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

/** 解析 shell 风格参数串（支持双引号/单引号包裹的值，如 --auth-token "abc def"）。 */
function splitArgs(input) {
  const out = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m;
  while ((m = re.exec(input)) !== null) {
    out.push(m[1] !== undefined ? m[1] : m[2] !== undefined ? m[2] : m[3]);
  }
  return out;
}

/** 从附加参数中提取 --auth-token（支持 --auth-token <值> 与 --auth-token=<值>）。 */
function extractAuthToken(args) {
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === '--auth-token') {
      if (i + 1 < args.length && !args[i + 1].startsWith('-')) return args[i + 1];
      return '';
    }
    if (arg.startsWith('--auth-token=')) return arg.slice('--auth-token='.length);
  }
  return '';
}

/** 带认证头的 HTTP GET（JSON 解析容错）。 */
function httpGet(url, headers) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { headers }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        let parsed = null;
        try { parsed = JSON.parse(body); } catch (_err) { /* 非 JSON 响应 */ }
        resolve({ status: res.statusCode, body: parsed, raw: body });
      });
    });
    req.setTimeout(5000, () => req.destroy(new Error('http request timeout')));
    req.on('error', reject);
  });
}

function authHeaders() {
  return backendAuthToken ? { 'X-Auth-Token': backendAuthToken } : {};
}

// ---------------------------------------------------------------------------
// 后端进程
// ---------------------------------------------------------------------------
/** 解析后端启动方式：优先随包分发的 agent-cluster-backend.exe，否则回退 uv run。 */
function resolveBackendLaunch() {
  const repoRoot = REPO_ROOT;
  const candidates = [];
  if (app.isPackaged && process.resourcesPath) {
    candidates.push(path.join(process.resourcesPath, 'agent-cluster-backend.exe'));
  }
  candidates.push(path.join(__dirname, 'resources', 'agent-cluster-backend.exe'));

  for (const exe of candidates) {
    if (fs.existsSync(exe)) {
      log(`使用随包后端：${exe}`);
      return { command: exe, args: [], cwd: repoRoot };
    }
  }

  log('未找到随包后端（agent-cluster-backend.exe），回退 uv run agent-cluster serve');
  return { command: 'uv', args: ['run', 'agent-cluster', 'serve'], cwd: repoRoot };
}

/** 轮询 /api/v1/status 等待后端就绪（最多约 30s）。 */
async function waitForBackendReady(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const res = await httpGet(`${url}/api/v1/status`, authHeaders());
      if (res.status >= 200 && res.status < 300) return true;
      lastError = new Error(`status ${res.status}`);
    } catch (err) {
      lastError = err;
    }
    await sleep(500);
  }
  throw lastError || new Error(`后端 ${timeoutMs}ms 内未就绪`);
}

async function startBackend() {
  const port = await getFreePort();
  const launch = resolveBackendLaunch();
  const extraArgs = splitArgs(process.env.AGENT_CLUSTER_SERVE_ARGS || '');
  const args = [...launch.args, '--port', String(port), ...extraArgs];

  log(`启动后端：${launch.command} ${args.join(' ')}（cwd=${launch.cwd}）`);
  backendAuthToken = extractAuthToken(extraArgs);

  backendProcess = spawn(launch.command, args, {
    cwd: launch.cwd,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });

  backendProcess.stdout.on('data', (chunk) => {
    for (const line of String(chunk).split(/\r?\n/)) {
      if (line.trim()) log('[backend]', line.trimEnd());
    }
  });
  backendProcess.stderr.on('data', (chunk) => {
    for (const line of String(chunk).split(/\r?\n/)) {
      if (line.trim()) console.error('[desktop] [backend:err]', line.trimEnd());
    }
  });
  backendProcess.on('error', (err) => {
    console.error('[desktop] 后端进程启动失败：', err.message);
  });
  backendProcess.on('exit', (code, signal) => {
    log(`后端进程退出 code=${code} signal=${signal}`);
    backendProcess = null;
    if (!isQuitting && !SMOKE_MODE) {
      dialog.showErrorBox('AgentClusterWorkbench', `agent-cluster serve 后端已退出（code=${code}）。\n请重新启动工作台。`);
    }
  });

  backendUrl = `http://127.0.0.1:${port}`;
  await waitForBackendReady(backendUrl, BACKEND_READY_TIMEOUT_MS);
  log(`后端就绪：${backendUrl}${backendAuthToken ? '（认证已启用）' : ''}`);
  return backendUrl;
}

/** 通过 netstat 找到监听指定端口的 PID（Windows）。 */
function findPidListeningOnPort(port) {
  try {
    const out = execFileSync('netstat', ['-ano', '-p', 'tcp'], {
      encoding: 'utf8',
      windowsHide: true,
    });
    for (const line of out.split(/\r?\n/)) {
      const m = line.match(/TCP\s+127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING\s+(\d+)/);
      if (m && Number(m[1]) === port) return Number(m[2]);
    }
  } catch (err) {
    log(`netstat 查询失败：${err.message}`);
  }
  return null;
}

/** 退出清理：kill 后端子进程树。Windows 下 uv.exe 可能提前退出，
 *  因此除 taskkill 直接父进程外，还按端口反查实际监听进程（agent-cluster.exe/python）并连树清理。 */
function killBackend() {
  const pid = backendProcess ? backendProcess.pid : null;
  if (pid) {
    log(`停止后端进程 pid=${pid}`);
    try {
      backendProcess.kill();
    } catch (err) {
      log(`child.kill 失败：${err.message}`);
    }
    if (process.platform === 'win32') {
      try {
        execFileSync('taskkill', ['/pid', String(pid), '/T', '/F'], {
          stdio: 'ignore',
          windowsHide: true,
        });
        log(`已用 taskkill 清理 pid=${pid} 的进程树`);
      } catch (err) {
        // uv.exe 可能已提前退出，交给下方端口反查兜底
        log(`taskkill 未命中（进程可能已退出）：${err.message}`);
      }
    }
  }
  if (process.platform === 'win32' && backendUrl) {
    const port = Number(new URL(backendUrl).port);
    if (port) {
      const listenerPid = findPidListeningOnPort(port);
      if (listenerPid && listenerPid !== pid) {
        try {
          execFileSync('taskkill', ['/pid', String(listenerPid), '/T', '/F'], {
            stdio: 'ignore',
            windowsHide: true,
          });
          log(`已按端口 ${port} 清理监听进程 pid=${listenerPid} 的进程树`);
        } catch (err) {
          log(`端口 ${port} 监听进程清理失败：${err.message}`);
        }
      }
    }
  }
  backendProcess = null;
}

// ---------------------------------------------------------------------------
// 窗口 / 托盘 / 快捷键 / 通知
// ---------------------------------------------------------------------------
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    show: false,
    title: 'AgentClusterWorkbench',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--agent-cluster-backend-url=${backendUrl}`],
    },
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    updater.markBootOk();
    updater.writeLastKnownGood();
  });
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
    }
  });
  mainWindow.on('closed', () => { mainWindow = null; });

  const prodIndex = path.join(__dirname, 'dist-frontend', 'index.html');
  if (app.isPackaged && fs.existsSync(prodIndex)) {
    log(`加载生产前端：${prodIndex}`);
    mainWindow.loadFile(prodIndex);
  } else {
    const devUrl = process.env.AGENT_CLUSTER_FRONTEND_URL || 'http://127.0.0.1:5173';
    log(`加载开发前端：${devUrl}`);
    mainWindow.loadURL(devUrl);
  }
}

function trayIcon() {
  const icon = nativeImage.createFromDataURL(`data:image/png;base64,${TRAY_ICON_B64}`);
  if (icon.isEmpty()) {
    // 极端情况下退回空图标（仍有文字菜单可用）
    return nativeImage.createEmpty();
  }
  return icon;
}

function createTray() {
  tray = new Tray(trayIcon());
  tray.setToolTip(TRAY_TOOLTIP);
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: '显示 / 隐藏工作台', click: toggleWindow },
      { type: 'separator' },
      {
        label: '退出',
        click: () => {
          isQuitting = true;
          app.quit();
        },
      },
    ])
  );
  tray.on('click', toggleWindow);
}

function toggleWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
    return;
  }
  if (mainWindow.isVisible()) {
    mainWindow.hide();
  } else {
    mainWindow.show();
    mainWindow.focus();
  }
}

function setupGlobalShortcut() {
  const ok = globalShortcut.register('Ctrl+Alt+K', toggleWindow);
  if (ok) {
    log('全局快捷键已注册：Ctrl+Alt+K');
  } else {
    log('全局快捷键注册失败（可能被其他程序占用）：Ctrl+Alt+K');
  }
}

function setupAutostart() {
  if (process.env.AGENT_CLUSTER_AUTOSTART === '1') {
    app.setLoginItemSettings({ openAtLogin: true });
    log('已启用开机自启（AGENT_CLUSTER_AUTOSTART=1）');
  } else {
    log('开机自启未启用（设置 AGENT_CLUSTER_AUTOSTART=1 开启）');
  }
}

/** 拉取当前所有 status=waiting_approval 的会话（先看 status 摘要，再按项目展开会话列表）。 */
async function findWaitingApprovalSessions() {
  const statusRes = await httpGet(`${backendUrl}/api/v1/status`, authHeaders());
  if (statusRes.status !== 200 || !statusRes.body || !statusRes.body.ok) return [];
  if (!statusRes.body.data || statusRes.body.data.active_sessions <= 0) return [];

  const projectsRes = await httpGet(`${backendUrl}/api/v1/projects`, authHeaders());
  const projects = (projectsRes.body && projectsRes.body.data) || [];
  const waiting = [];
  for (const project of projects) {
    if (!project || !project.id) continue;
    const sessionsRes = await httpGet(
      `${backendUrl}/api/v1/projects/${encodeURIComponent(project.id)}/sessions`,
      authHeaders()
    );
    const sessions = (sessionsRes.body && sessionsRes.body.data) || [];
    for (const session of sessions) {
      if (session && session.status === 'waiting_approval') waiting.push(session);
    }
  }
  return waiting;
}

/** 有会话进入 waiting_approval 时发系统通知（按会话去重 + 最小间隔防抖）。 */
function handleWaitingApproval(waiting) {
  if (!Notification.isSupported()) return;
  const now = Date.now();
  const fresh = waiting.filter((s) => s && s.session_id && !notifiedSessionIds.has(s.session_id));
  const currentIds = new Set(waiting.filter((s) => s && s.session_id).map((s) => s.session_id));

  // 不再等待的会话解除“已通知”标记，允许后续再次进入时提醒
  for (const id of notifiedSessionIds) {
    if (!currentIds.has(id)) notifiedSessionIds.delete(id);
  }

  if (fresh.length === 0) return;
  if (now - lastNotificationAt < NOTIFICATION_MIN_INTERVAL_MS) return;

  const notification = new Notification({
    title: TRAY_TOOLTIP,
    body: `${fresh.length} 个会话等待审批，点击查看`,
  });
  notification.on('click', () => {
    toggleWindow();
  });
  notification.show();
  lastNotificationAt = now;
  for (const s of fresh) notifiedSessionIds.add(s.session_id);
  log(`已发送等待审批通知：${fresh.map((s) => s.session_id).join(', ')}`);
}

async function pollForApprovals() {
  while (!isQuitting) {
    try {
      if (backendUrl) {
        const waiting = await findWaitingApprovalSessions();
        if (waiting.length > 0) handleWaitingApproval(waiting);
      }
    } catch (err) {
      // 后端偶发不可达时静默跳过，下一轮再试
      log(`审批轮询失败（忽略）：${err.message}`);
    }
    await sleep(STATUS_POLL_INTERVAL_MS);
  }
}

/** 自动更新状态提示：版本钉扎强制升级 / 下载完成重启安装。 */
function handleUpdateStatus(status) {
  if (!status || !mainWindow || mainWindow.isDestroyed()) return;
  if (status.state === 'update-required') {
    dialog.showMessageBoxSync(mainWindow, {
      type: 'warning',
      title: '版本过旧',
      message: `当前版本低于最低要求 ${status.minimumVersion}`,
      detail: '请前往 GitHub Releases 下载最新版本，或稍后重启工作台自动更新。',
      buttons: ['知道了'],
      defaultId: 0,
    });
  } else if (status.state === 'update-downloaded') {
    const choice = dialog.showMessageBoxSync(mainWindow, {
      type: 'info',
      title: '更新已就绪',
      message: `新版本 ${status.version} 已下载完成`,
      detail: '重启应用即可完成安装。',
      buttons: ['立即重启安装', '稍后'],
      defaultId: 0,
      cancelId: 1,
    });
    if (choice === 0) updater.quitAndInstall();
  }
}

// ---------------------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------------------
app.whenReady().then(async () => {
  updater.init();
  updater.registerIpc();
  if (!SMOKE_MODE) {
    updater.watchdogStartup();
    updater.onStatus(handleUpdateStatus);
  }
  try {
    await startBackend();
  } catch (err) {
    console.error('[desktop] 后端启动失败：', err);
    if (!SMOKE_MODE) {
      dialog.showErrorBox('AgentClusterWorkbench', `后端启动失败：${err.message}`);
    }
    app.exit(1);
    return;
  }

  if (SMOKE_MODE) {
    log('SMOKE OK：后端已就绪，退出冒烟模式');
    app.quit();
    return;
  }

  createWindow();
  createTray();
  setupGlobalShortcut();
  setupAutostart();
  pollForApprovals();
});

// 托盘应用：窗口全部关闭后仍驻留，由托盘“退出”或系统退出结束
app.on('window-all-closed', () => {
  // 不退出：保持托盘常驻
});

app.on('activate', () => {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
  } else {
    mainWindow.show();
  }
});

app.on('before-quit', () => {
  isQuitting = true;
});

app.on('will-quit', () => {
  killBackend();
  globalShortcut.unregisterAll();
});