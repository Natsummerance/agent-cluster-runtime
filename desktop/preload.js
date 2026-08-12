'use strict';

// 最小 contextBridge：仅暴露后端地址给渲染进程（React 工作台通过 window.agentCluster 获取）。
const { contextBridge } = require('electron');

// 地址由主进程通过 webPreferences.additionalArguments 注入
const backendArg = process.argv.find((arg) => arg.startsWith('--agent-cluster-backend-url='));
const backendUrl = backendArg ? backendArg.slice('--agent-cluster-backend-url='.length) : '';

contextBridge.exposeInMainWorld('agentCluster', {
  getBackendUrl: () => backendUrl,
});