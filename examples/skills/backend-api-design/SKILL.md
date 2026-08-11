---
name: backend-api-design
description: 后端 API 设计技能：REST/OpenAPI 契约、错误码与幂等性设计。
version: 2.1.0
license: MIT
allowed-tools:
  - read_file
  - write_file
  - bash
---
# 后端 API 设计指引

1. 先定义 OpenAPI 契约再实现。
2. 统一错误码结构与错误响应体。
3. 写操作需声明幂等键（Idempotency-Key）。
