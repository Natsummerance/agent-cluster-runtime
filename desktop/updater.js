'use strict';

// updater.js —— 自动更新模块（electron-updater + GitHub Releases provider）
//
// 双通道：stable（正式 Release v*）/ latest（prerelease）
// 设置：autoUpdateEnabled（默认开）+ 通道（默认 stable），持久化到 userData/update-settings.json
// 失败回退：安装失败 → 删除坏版本目录（resources/app-<version>）→ 回退 lastKnownGood
//   （userData 记录版本+路径，userData 永不回滚）
// 版本钉扎：拉取 latest*.yml 解析 minimumVersion，低于钉扎版本 → update-required 强制升级提示
// 启动看门狗：窗口 ready 后写 boot-ok；下次启动缺失 → 回退 lastKnownGood
//
// 只暴露最小 IPC 面（get/setUpdateSettings/checkForUpdates/onUpdateStatus），
// 不开通任意进程能力；安装/重启由主进程对话框触发。

const { app } = require('electron');
const path = require('path');
const fs = require('fs');
const https = require('https');

const SETTINGS_FILE = 'update-settings.json';
const LAST_KNOWN_GOOD_FILE = 'last-known-good.json';
const BOOT_OK_FILE = 'boot-ok';
const DEFAULT_REPO = 'natsummerance/agent-cluster-runtime';
const FEED_HOST = 'api.github.com';

let autoUpdater = null;
let settings = null;
let lastStatus = { state: 'idle' };
const statusCallbacks = [];

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
function log(...args) {
  console.log('[updater]', ...args);
}

function userDataPath(name) {
  return path.join(app.getPath('userData'), name);
}

// 扁平 YAML 子集解析：只解析本仓库 electron-builder 产出的固定 key
// （version/minimumVersion/unsigned/files[].{url,sha512,size}）。
function parseFlatYaml(text) {
  const result = { files: [] };
  let currentFile = null;
  for (const rawLine of String(text).split(/\r?\n/)) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    if (/^files:\s*$/.test(trimmed)) {
      currentFile = null;
      continue;
    }
    const item = /^-\s+url:\s*(.+)$/.exec(trimmed);
    if (item) {
      currentFile = { url: item[1].trim() };
      result.files.push(currentFile);
      continue;
    }
    const kv = /^([A-Za-z0-9_.-]+):\s*(.*)$/.exec(trimmed);
    if (!kv) continue;
    if (currentFile && (kv[1] === 'sha512' || kv[1] === 'size')) {
      currentFile[kv[1]] = kv[2].trim();
    } else if (!currentFile) {
      result[kv[1]] = kv[2].trim();
    }
  }
  return result;
}

/** 语义化版本比较：a < b → -1；a == b → 0；a > b → 1。 */
function compareVersions(a, b) {
  const pa = String(a).replace(/^v/, '').split('.').map((n) => parseInt(n, 10) || 0);
  const pb = String(b).replace(/^v/, '').split('.').map((n) => parseInt(n, 10) || 0);
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i += 1) {
    const da = pa[i] || 0;
    const db = pb[i] || 0;
    if (da !== db) return da < db ? -1 : 1;
  }
  return 0;
}

/** HTTPS GET 文本（跟随 3xx 重定向，超时 10s）。 */
function httpsGetText(url, headers) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { 'User-Agent': 'AgentClusterWorkbench', ...headers } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        httpsGetText(res.headers.location, headers).then(resolve, reject);
        return;
      }
      if (res.statusCode !== 200) {
        res.resume();
        reject(new Error(`HTTP ${res.statusCode} (${url})`));
        return;
      }
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve(body));
    });
    req.setTimeout(10000, () => req.destroy(new Error('更新请求超时')));
    req.on('error', reject);
  });
}

// ---------------------------------------------------------------------------
// 设置（userData/update-settings.json，userData 永不回滚）
// ---------------------------------------------------------------------------
function loadSettings() {
  try {
    const parsed = JSON.parse(fs.readFileSync(userDataPath(SETTINGS_FILE), 'utf8'));
    return {
      autoUpdateEnabled: parsed.autoUpdateEnabled !== false,
      channel: parsed.channel === 'latest' ? 'latest' : 'stable',
    };
  } catch (_err) {
    return { autoUpdateEnabled: true, channel: 'stable' };
  }
}

function saveSettings(next) {
  settings = {
    autoUpdateEnabled: next.autoUpdateEnabled !== false,
    channel: next.channel === 'latest' ? 'latest' : 'stable',
  };
  try {
    fs.writeFileSync(userDataPath(SETTINGS_FILE), JSON.stringify(settings, null, 2), 'utf8');
  } catch (err) {
    log(`保存更新设置失败：${err.message}`);
  }
  return settings;
}// ---------------------------------------------------------------------------
// lastKnownGood / boot-ok 看门狗
// ---------------------------------------------------------------------------
function readLastKnownGood() {
  try {
    return JSON.parse(fs.readFileSync(userDataPath(LAST_KNOWN_GOOD_FILE), 'utf8'));
  } catch (_err) {
    return null;
  }
}

function writeLastKnownGood() {
  const record = {
    version: app.getVersion(),
    path: app.getAppPath(),
    at: new Date().toISOString(),
  };
  try {
    fs.writeFileSync(userDataPath(LAST_KNOWN_GOOD_FILE), JSON.stringify(record, null, 2), 'utf8');
  } catch (err) {
    log(`写入 lastKnownGood 失败：${err.message}`);
  }
  return record;
}

function markBootOk() {
  try {
    fs.writeFileSync(userDataPath(BOOT_OK_FILE), 'ok', 'utf8');
  } catch (err) {
    log(`写入 boot-ok 失败：${err.message}`);
  }
}

/** 删除 resources 下非当前版本的 app-<version> 残留目录（electron-updater 双目录机制）。 */
function removeBadVersionDirs() {
  const resourcesDir = path.dirname(app.getAppPath());
  let removed = 0;
  try {
    for (const entry of fs.readdirSync(resourcesDir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const m = /^app-([\d.]+)(?:-[a-z0-9-]+)?$/i.exec(entry.name);
      if (m && m[1] !== app.getVersion()) {
        const target = path.join(resourcesDir, entry.name);
        fs.rmSync(target, { recursive: true, force: true });
        removed += 1;
        log(`已删除坏版本目录：${target}`);
      }
    }
  } catch (err) {
    log(`清理坏版本目录失败：${err.message}`);
  }
  return removed;
}

/**
 * 启动看门狗：上次启动未写 boot-ok（窗口未就绪即崩溃/被杀）→ 回退 lastKnownGood。
 * 返回 { rolledBack: boolean, version?: string }。
 */
function watchdogStartup() {
  const bootOkExists = fs.existsSync(userDataPath(BOOT_OK_FILE));
  try {
    fs.rmSync(userDataPath(BOOT_OK_FILE), { force: true }); // 本次启动重新计
  } catch (_err) { /* 忽略 */ }
  if (bootOkExists) return { rolledBack: false };
  const lkg = readLastKnownGood();
  if (!lkg || lkg.version === app.getVersion()) return { rolledBack: false };
  const removed = removeBadVersionDirs();
  emitStatus({ state: 'rolled-back', version: lkg.version, removed });
  log(`上次启动异常：回退 lastKnownGood ${lkg.version}（清理目录 ${removed} 个）`);
  return { rolledBack: true, version: lkg.version };
}

// ---------------------------------------------------------------------------
// 状态事件（渲染进程 + 主进程对话框共用）
// ---------------------------------------------------------------------------
function emitStatus(status) {
  lastStatus = status;
  for (const cb of statusCallbacks.slice()) {
    try { cb(status); } catch (err) { log(`状态回调异常：${err.message}`); }
  }
  try {
    const { BrowserWindow } = require('electron');
    for (const win of BrowserWindow.getAllWindows()) {
      if (!win.isDestroyed()) win.webContents.send('update:status', status);
    }
  } catch (_err) { /* 无窗口（如看门狗在启动早期触发） */ }
}

/** 订阅更新状态（返回退订函数）。 */
function onStatus(callback) {
  statusCallbacks.push(callback);
  return () => {
    const idx = statusCallbacks.indexOf(callback);
    if (idx >= 0) statusCallbacks.splice(idx, 1);
  };
}// ---------------------------------------------------------------------------
// 版本钉扎：拉取 latest*.yml 解析 minimumVersion
// ---------------------------------------------------------------------------
function feedFileName() {
  if (process.platform === 'darwin') return 'latest-mac.yml';
  if (process.platform === 'linux') return 'latest-linux.yml';
  return 'latest.yml';
}

function updateRepo() {
  return process.env.AGENT_CLUSTER_UPDATE_REPO || DEFAULT_REPO;
}

/** latest 通道：从 GitHub API 找最新（含 prerelease）的非 draft Release tag。 */
async function fetchLatestReleaseTag() {
  const body = await httpsGetText(`https://${FEED_HOST}/repos/${updateRepo()}/releases?per_page=20`, {
    Accept: 'application/vnd.github+json',
  });
  const releases = JSON.parse(body);
  if (!Array.isArray(releases)) return null;
  const release = releases.find((r) => r && r.draft === false && r.tag_name && Array.isArray(r.assets));
  return release ? release.tag_name : null;
}

async function fetchFeedYaml() {
  const repo = updateRepo();
  const feed = feedFileName();
  if (settings.channel === 'latest') {
    const tag = await fetchLatestReleaseTag();
    if (!tag) return null;
    return httpsGetText(`https://github.com/${repo}/releases/download/${encodeURIComponent(tag)}/${feed}`);
  }
  return httpsGetText(`https://github.com/${repo}/releases/latest/download/${feed}`);
}

/** 解析远端 minimumVersion；网络失败/无发布时返回 null（不阻断普通检查）。 */
async function checkMinimumVersion() {
  try {
    const yamlText = await fetchFeedYaml();
    if (!yamlText) return null;
    const meta = parseFlatYaml(yamlText);
    return meta.minimumVersion || null;
  } catch (err) {
    log(`获取版本钉扎信息失败：${err.message}`);
    return null;
  }
}

// ---------------------------------------------------------------------------
// electron-updater 装配 + 检查/安装
// ---------------------------------------------------------------------------
function init() {
  settings = loadSettings();
  try {
    autoUpdater = require('electron-updater').autoUpdater;
    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.channel = settings.channel;
    autoUpdater.on('checking-for-update', () => emitStatus({ state: 'checking' }));
    autoUpdater.on('update-available', (info) =>
      emitStatus({ state: 'update-available', version: info && info.version }));
    autoUpdater.on('update-not-available', (info) =>
      emitStatus({ state: 'update-not-available', version: info && info.version }));
    autoUpdater.on('error', (err) =>
      emitStatus({ state: 'error', message: String((err && err.message) || err) }));
    autoUpdater.on('update-downloaded', (info) =>
      emitStatus({ state: 'update-downloaded', version: info && info.version }));
  } catch (err) {
    log(`electron-updater 加载失败（开发模式常见）：${err.message}`);
  }
}

async function checkForUpdates() {
  if (!autoUpdater) {
    emitStatus({ state: 'error', message: '更新模块不可用（开发模式）' });
    return { ok: false, reason: 'unavailable' };
  }
  if (!settings.autoUpdateEnabled) {
    emitStatus({ state: 'disabled' });
    return { ok: false, reason: 'disabled' };
  }
  const minVersion = await checkMinimumVersion();
  if (minVersion && compareVersions(app.getVersion(), minVersion) < 0) {
    emitStatus({ state: 'update-required', minimumVersion: minVersion });
    return { ok: false, reason: 'minimum-version', minimumVersion: minVersion };
  }
  try {
    autoUpdater.channel = settings.channel;
    await autoUpdater.checkForUpdates();
    return { ok: true };
  } catch (err) {
    emitStatus({ state: 'error', message: String((err && err.message) || err) });
    return { ok: false, reason: 'check-failed' };
  }
}

function downloadUpdate() {
  if (autoUpdater) autoUpdater.downloadUpdate();
}

function quitAndInstall() {
  if (autoUpdater) autoUpdater.quitAndInstall(false, true);
}

// ---------------------------------------------------------------------------
// IPC（最小面：get/setUpdateSettings、checkForUpdates、onUpdateStatus）
// ---------------------------------------------------------------------------
function registerIpc() {
  try {
    const { ipcMain } = require('electron');
    ipcMain.handle('update:getSettings', () => settings);
    ipcMain.handle('update:setSettings', (_event, next) => {
      const saved = saveSettings(next || {});
      if (autoUpdater) autoUpdater.channel = saved.channel;
      return saved;
    });
    ipcMain.handle('update:check', async () => {
      const result = await checkForUpdates();
      return { ...result, status: lastStatus };
    });
  } catch (err) {
    log(`IPC 注册失败：${err.message}`);
  }
}

module.exports = {
  init,
  registerIpc,
  onStatus,
  checkForUpdates,
  downloadUpdate,
  quitAndInstall,
  markBootOk,
  writeLastKnownGood,
  watchdogStartup,
};