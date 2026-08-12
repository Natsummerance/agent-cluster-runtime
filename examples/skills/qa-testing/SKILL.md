---
name: qa-testing
description: 测试质量保障技能：测试计划、用例设计、自动化执行与缺陷回归。
version: 1.0.0
license: MIT
allowed-tools:
  - read_file
  - run_tests
  - review
---
# 测试执行指引

1. 依据验收标准编写测试计划与用例（Given/When/Then 格式）。
2. 优先自动化冒烟与回归，覆盖边界条件与异常路径。
3. 缺陷单须含复现步骤、期望/实际结果与优先级，回归通过后关闭。