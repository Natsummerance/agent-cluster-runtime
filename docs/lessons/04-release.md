# 04-release —— electron-builder / GitHub Release

> 何时加载：打包、上传 Release、metadata 补丁、资产核验时**必读**。

## mac 双架构产物重复（release 422 的根因）
- 现象：`gh release create` 上传报 `HTTP 422 Validation Failed`（同名资产）。
- 根因：`electron-builder.yml` 的 `mac.target` 固定 `arch: [arm64, x64]` 时，CLI `--arm64/--x64`
  不会收窄 → 矩阵两个 mac job 产出完全相同文件。
- 修复（勿回退）：mac target 只写 `dmg`/`zip`，架构由 CLI 决定；release 上传按 basename 去重兜底。

## 上传通道
- softprops/action-gh-release 上传后 metadata PATCH 有 404 竞态（`error updating release asset
  metadata ... Not Found`）→ 已弃用，改 `gh release` CLI：
  `gh release delete "$TAG" --yes --cleanup-tag=false || true` + `gh release create`（`--generate-notes`）。
- 资产只传安装包 glob（`*.exe/*.blockmap/*.dmg/*.zip/*.deb/latest*.yml`），曾把 unpacked 目录整棵
  传上去导致限流/垃圾资产。
- Windows 产物名含空格 → release job 先 `tr ' ' '-'` 归一化，与 `latest.yml` url 一致。

## metadata 补丁
- `desktop/scripts/patch-update-metadata.js --out-dir metadata <ver> <latest*.yml>` 注入
  `minimumVersion`（全平台）+ `unsigned: true`（mac）。
- 上传前先 `find artifacts -name 'latest*.yml' -delete`（metadata/ 补丁版权威）。

## 打包环境
- package job `GITHUB_TOKEN` 显式置空（决策 D24），防 electron-builder 推断 publish provider。
- macOS job 设 `UPDATER_SUFFIX=-unsigned`（无签名证书轮次）。

## 资产核验清单（发布后必查，用 GitHub API）
1. 资产数 = 18（win 6 + mac arm64 4 + mac x64 4 + linux 1 + latest*.yml 3），无重复、无 unpacked 目录。
2. `latest.yml` / `latest-linux.yml` / `latest-mac.yml` 均含 `minimumVersion: <版本>`；`latest-mac.yml` 含 `unsigned: true`。
3. 核验用 `Select-Object name,size` 列资产 + 下载 3 个 yml 检查内容（小文件，token 可接受）。

## CI 打包必须自带前端（dist-frontend 缺口，v0.6.4 修复）
- `desktop/dist-frontend` 被 gitignore：CI checkout 不含前端产物；package job 此前只跑 `npm ci + electron-builder`，
  `files: dist-frontend/**` 空 glob 静默跳过 → v0.6.2/v0.6.3 CI 安装包大概率无工作台 UI（白屏）。
- 修复：package job 在 electron-builder 前加 `setup-node(frontend lock) → npm ci && npm run build` +
  `shell: bash` 的 `rm -rf desktop/dist-frontend && mkdir -p ... && cp -r frontend/dist/* ...`（三平台通用）。
- 本地按 D26 顺序：验收 → 前端 build 复制进 desktop/dist-frontend → Electron smoke → NSIS → tag。
- 核验安装包含 UI：`@electron/asar` listPackage `win-unpacked/resources/app.asar`（路径分隔符是 `\`，判断用 `.replace(/\\/g,'/')`）。

## release job 下载范围（diagnostics 垃圾资产，v0.6.4 实测）
- release job `download-artifact` 不写 pattern 会把失败 job 的 `diagnostics-*` artifact 一起下载；
  release 上传用 `find artifacts -type f` → `pytest-output.log` 被当 Release 资产传上去（v0.6.4 多出第 19 个资产）。
- 修复：`download-artifact` 加 `pattern: package-*`；发布后核验资产数 = 18，发现多余资产用
  `DELETE /repos/{owner}/{repo}/releases/assets/{id}` 清除。
