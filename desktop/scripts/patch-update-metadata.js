'use strict';

// patch-update-metadata.js —— 向 electron-builder 产出的 latest*.yml 注入自动更新元数据
//
// 用法:
//   node scripts/patch-update-metadata.js <minimumVersion> <latest*.yml...>
//
// 行为（设计 §13：版本钉扎 + unsigned 标记）:
//   - 每个输入文件注入/更新 `minimumVersion: <version>`（Claude Code minimumVersion 对标）。
//   - 当产物 url 含 "-unsigned"（macOS 无证书构建）时，latest-mac.yml 注入 `unsigned: true`。
//   - 多个同名 latest-mac.yml（arm64/x64 两个 CI job 各自产出）按 url 去重合并为一个文件。
//   - 只解析本仓库 electron-builder 产出的固定扁平 YAML 子集 key，不做通用 YAML 解析。
//   - 原位写回（UTF-8、LF）。

const fs = require('fs');
const path = require('path');

// ---------------------------------------------------------------------------
// 扁平 YAML 子集：切分为 header / files 块 / trailer
// ---------------------------------------------------------------------------
function splitYaml(text) {
  const lines = String(text).split(/\r?\n/);
  const header = [];
  const fileBlocks = [];
  const trailer = [];
  let i = 0;
  while (i < lines.length && !/^files:\s*$/.test(lines[i].trim())) {
    header.push(lines[i]);
    i += 1;
  }
  if (i >= lines.length) return { header, fileBlocks, trailer };
  i += 1; // 跳过 files:
  while (i < lines.length) {
    const line = lines[i];
    if (/^-\s+url:/.test(line.trim())) {
      const block = [line];
      i += 1;
      while (i < lines.length && /^\s+/.test(lines[i]) && !/^-\s+/.test(lines[i].trim())) {
        block.push(lines[i]);
        i += 1;
      }
      fileBlocks.push(block);
    } else if (/^[A-Za-z0-9_.-]+:\s*/.test(line.trim())) {
      break; // 回到顶层 key
    } else {
      i += 1;
    }
  }
  for (; i < lines.length; i += 1) trailer.push(lines[i]);
  return { header, fileBlocks, trailer };
}

function joinYaml({ header, fileBlocks, trailer }) {
  const parts = [];
  const headerText = header.filter((l) => l !== '').join('\n');
  if (headerText) parts.push(headerText);
  if (fileBlocks.length > 0) {
    parts.push('files:');
    for (const block of fileBlocks) parts.push(block.join('\n'));
  }
  const trailerText = trailer.filter((l) => l !== '').join('\n');
  if (trailerText) parts.push(trailerText);
  return parts.join('\n') + '\n';
}

// ---------------------------------------------------------------------------
// 注入 minimumVersion / unsigned
// ---------------------------------------------------------------------------
function injectMetadata(text, minimumVersion) {
  const { header, fileBlocks, trailer } = splitYaml(text);
  const hasUnsignedUrl = fileBlocks.some((block) =>
    block.some((l) => /^-\s+url:/.test(l.trim()) && /unsigned/i.test(l.trim()))
  );
  const isMac = fileBlocks.some((block) => block.some((l) => /\.zip|\.dmg/i.test(l)));

  const patchKey = (lines, key, value) => {
    let replaced = false;
    const out = [];
    for (const line of lines) {
      const m = /^([A-Za-z0-9_.-]+):\s*(.*)$/.exec(line.trim());
      if (m && m[1] === key) {
        out.push(`${key}: ${value}`);
        replaced = true;
      } else {
        out.push(line);
      }
    }
    if (!replaced) out.push(`${key}: ${value}`);
    return out;
  };

  const newHeader = patchKey(header, 'minimumVersion', minimumVersion);
  return joinYaml({ header: isMac && hasUnsignedUrl ? patchKey(newHeader, 'unsigned', 'true') : newHeader, fileBlocks, trailer });
}

// ---------------------------------------------------------------------------
// 合并同名 yml（mac arm64/x64 各自产物）→ 按 url 去重的 files 列表
// ---------------------------------------------------------------------------
function mergeSameName(inputs) {
  if (inputs.length === 1) return splitYaml(fs.readFileSync(inputs[0], 'utf8'));
  const merged = { header: [], fileBlocks: [], trailer: [] };
  const seenUrls = new Set();
  let first = true;
  for (const input of inputs) {
    const doc = splitYaml(fs.readFileSync(input, 'utf8'));
    if (first) {
      merged.header = doc.header;
      merged.trailer = doc.trailer;
      first = false;
    }
    for (const block of doc.fileBlocks) {
      const urlLine = block.find((l) => /^-\s+url:/.test(l.trim()));
      const url = urlLine ? urlLine.trim() : '';
      if (!seenUrls.has(url)) {
        seenUrls.add(url);
        merged.fileBlocks.push(block);
      }
    }
  }
  return merged;
}

// ---------------------------------------------------------------------------
// 主流程
// ---------------------------------------------------------------------------
function main(argv) {
  if (argv.length < 3) {
    console.error('用法: node patch-update-metadata.js <minimumVersion> <latest*.yml...>');
    process.exit(1);
  }
  const [minimumVersion, ...inputs] = argv.slice(2);
  const byName = new Map();
  for (const input of inputs) {
    const base = path.basename(input);
    if (!byName.has(base)) byName.set(base, []);
    byName.get(base).push(input);
  }
  for (const [base, files] of byName) {
    const merged = mergeSameName(files);
    const text = joinYaml(merged);
    const patched = injectMetadata(text, minimumVersion);
    const target = files.length === 1 ? files[0] : path.join(path.dirname(files[0]), base);
    fs.writeFileSync(target, patched, { encoding: 'utf8' });
    console.log(`[patch-update-metadata] ${target}: minimumVersion=${minimumVersion}` +
      (patched.includes('unsigned: true') ? ', unsigned=true' : ''));
  }
}

main(process.argv);