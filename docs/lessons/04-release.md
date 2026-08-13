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