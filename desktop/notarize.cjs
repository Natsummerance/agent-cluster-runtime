'use strict';

// notarize.cjs —— macOS 条件公证脚本（electron-builder afterSign / afterAllArtifactBuild 钩子）
//
// 规则（设计 §13，seren 教训：dmg 与 zip 必须同时公证，只公证 dmg 会让 updater 拉取的 zip 被判损坏）：
//   - 仅当环境变量 NOTARIZE=1 时执行公证；否则直接退出 0（无证书/未启用时绝不拦截构建）。
//   - 公证要求 APPLE_ID / APPLE_APP_PASSWORD / APPLE_TEAM_ID 三个环境变量齐备，缺失则报错。
//   - afterSign：对 .app 执行 notarytool submit --wait + stapler staple。
//   - afterAllArtifactBuild：对 dmg / zip 逐个执行 stapler staple。
//   - 产物名追加 "-unsigned" 由 electron-builder.yml 的 ${env.UPDATER_SUFFIX} 宏控制
//     （CI 无证书时置 UPDATER_SUFFIX=-unsigned；latest-mac.yml 的 unsigned: true 由
//     scripts/patch-update-metadata.js 在 release 时按产物名自动注入）。

const { execFileSync } = require('child_process');
const path = require('path');

function run(cmd, args) {
  execFileSync(cmd, args, { stdio: 'inherit' });
}

async function notarizeApp(appPath) {
  const appleId = process.env.APPLE_ID;
  const appleAppPassword = process.env.APPLE_APP_PASSWORD;
  const teamId = process.env.APPLE_TEAM_ID;
  if (!appleId || !appleAppPassword || !teamId) {
    throw new Error('NOTARIZE=1 但缺少 APPLE_ID / APPLE_APP_PASSWORD / APPLE_TEAM_ID，无法公证');
  }
  console.log(`[notarize] 提交公证：${appPath}`);
  run('xcrun', [
    'notarytool', 'submit', appPath,
    '--apple-id', appleId,
    '--apple-password', appleAppPassword,
    '--team-id', teamId,
    '--wait',
  ]);
  console.log('[notarize] 公证通过，staple .app');
  run('xcrun', ['stapler', 'staple', appPath]);
}

async function stapleArtifacts(artifactPaths) {
  for (const artifact of artifactPaths) {
    if (artifact.endsWith('.dmg') || artifact.endsWith('.zip')) {
      console.log(`[notarize] staple ${artifact}`);
      run('xcrun', ['stapler', 'staple', artifact]);
    }
  }
}

exports.default = async function notarizeHook(context) {
  if (process.env.NOTARIZE !== '1') {
    console.log('[notarize] NOTARIZE 未启用，跳过公证（无证书构建，产物将带 -unsigned 标记）');
    return;
  }
  if (context && Array.isArray(context.artifactPaths)) {
    await stapleArtifacts(context.artifactPaths);
    return;
  }
  const appOutDir = context && context.appOutDir;
  if (!appOutDir) {
    throw new Error('notarize.cjs 只能作为 afterSign / afterAllArtifactBuild 钩子使用');
  }
  const appName = context.packager.appInfo.productFilename;
  await notarizeApp(path.join(appOutDir, `${appName}.app`));
};