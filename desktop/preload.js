'use strict';

// 最小 contextBridge：仅暴露后端地址与自动更新最小 IPC 面给渲染进程
// （React 工作台通过 window.agentCluster 访问；不开通任意进程能力）。
const { contextBridge, ipcRenderer } = require('electron');

// 地址由主进程通过 webPreferences.additionalArguments 注入
const backendArg = process.argv.find((arg) => arg.startsWith('--agent-cluster-backend-url='));
const backendUrl = backendArg ? backendArg.slice('--agent-cluster-backend-url='.length) : '';

contextBridge.exposeInMainWorld('agentCluster', {
  getBackendUrl: () => backendUrl,
  // 自动更新：设置读写（autoUpdateEnabled/channel）、手动检查、状态订阅
  getUpdateSettings: () => ipcRenderer.invoke('update:getSettings'),
  setUpdateSettings: (next) => ipcRenderer.invoke('update:setSettings', next),
  checkForUpdates: () => ipcRenderer.invoke('update:check'),
  onUpdateStatus: (callback) => {
    const listener = (_event, status) => callback(status);
    ipcRenderer.on('update:status', listener);
    return () => ipcRenderer.removeListener('update:status', listener);
  },
});