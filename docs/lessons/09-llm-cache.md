# 09-llm-cache —— DeepSeek 上下文缓存机制（T14.6 实测）

> 何时加载：任何命中率优化、带 key LLM 测试、前缀装配改动。

## 实测机制（2026-08-14，真实 API）
- 缓存按 **~256 token 块**粒度：命中 token 数 = floor(稳定前缀/256)×256；每轮新内容
  所在块必 miss（新内容 + 块余量，约 10 token/轮 + 至多 ~255）。
- **小会话（前缀 <1 块）恒 0 命中**——不是实现 bug，是块粒度特性。
- 稳态命中率 ≈ 稳定前缀 / (稳定前缀 + 块余量 + 新内容)。≥98% 需要前缀 ≥ ~5KB
  （~20KB 前缀实测稳态 99.7%）。
- `x-deepseek-harness-session-id` 头不影响块行为，但保留（provider 局域性契约）。
- 首轮冷启动不计入稳态；usage 字段：`prompt_cache_hit_tokens` /
  `prompt_cache_miss_tokens`（Anthropic：`cache_read_input_tokens` /
  `cache_creation_input_tokens`）。
