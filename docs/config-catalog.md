# 配置目录（config-catalog）

> 由 `scripts/gen_config_catalog.py` 生成，勿手改；`scripts/verify_config_catalog.py` 校验 freshness。
> 语义：profile 行按 id 整块替换 + disabled（对照 dsh 配置分层契约，见 `docs/porting/`）。

## profile: serve

| id | disabled | 配置 |
|---|---|---|
| server | False | `host=127.0.0.1, port=8765` |
| api | False | `enabled=True, auth=local` |
| ui | False | `enabled=True` |
| llm | False | `provider=deepseek, model=deepseek-v4-flash` |
| tools | False | `enabled=True` |
| persistence | False | `backend=jsonl` |
| audit | False | `enabled=True` |

## profile: chat

| id | disabled | 配置 |
|---|---|---|
| repl | False | `enabled=True` |
| llm | False | `provider=deepseek, model=deepseek-v4-flash` |
| tools | False | `enabled=True` |
| persistence | False | `backend=memory` |

## profile: headless

| id | disabled | 配置 |
|---|---|---|
| runner | False | `enabled=True` |
| code-runtime | False | `enabled=True` |
| llm | False | `provider=deepseek, model=deepseek-v4-flash` |
| server | True | `enabled=False` |
