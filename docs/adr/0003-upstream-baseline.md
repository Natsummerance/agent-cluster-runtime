# ADR-0003: 冻结 DeepSeek Harness/Cordis 参考基线

- 状态：Accepted
- 日期：2026-08-17

## 决策

参考基线固定为 `deepseek-ai/deepseek-harness@47f943859bef60e4160492346772ded9b24f765a`（`0.1.0-rc.5`，MIT），其中 Cordis 包版本为 `4.0.1`。

DoAI 可复用、移植或改写其代码、契约、算法与测试，但每一项都必须记录来源文件、commit、许可、动作、有意偏差和验证方法。不会自动追随上游预览版本；升级基线必须新增 ADR、刷新许可证并重跑差分测试。

`docs/porting/dsh-provenance.yaml` 是机器可检查的溯源账本。第三方 MIT 文本保留在发布产物的 notices 中；没有溯源记录的复制代码不得合入。
