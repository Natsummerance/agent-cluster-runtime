#!/usr/bin/env node
// i18n:check —— 比较 en-US.json 与 zh-CN.json 的 key 集合（深度扁平化），缺 key / 多 key 退出 1
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => {
  const raw = readFileSync(join(root, 'src', 'i18n', 'messages', `${name}.json`), 'utf8');
  return JSON.parse(raw);
};

function flatten(obj, prefix = '', out = new Set()) {
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === 'object') {
      flatten(value, path, out);
    } else {
      out.add(path);
    }
  }
  return out;
}

const en = flatten(read('en-US'));
const zh = flatten(read('zh-CN'));
const missingZh = [...en].filter((key) => !zh.has(key));
const extraZh = [...zh].filter((key) => !en.has(key));
if (missingZh.length || extraZh.length) {
  for (const key of missingZh) console.error(`i18n: zh-CN.json 缺少 key: ${key}`);
  for (const key of extraZh) console.error(`i18n: zh-CN.json 多出 key: ${key}`);
  process.exit(1);
}
console.log(`i18n: en-US \u2194 zh-CN key sets match (${en.size} keys)`);
