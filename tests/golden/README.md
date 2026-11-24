# Agent-Harness Golden Cases

本目录位于 Harness Evaluation 与 CI 发布门禁之间：`cases.json` 保存版本化回归输入，
`schema.json` 描述字段约束。`mode=mock` 只允许由测试显式注入执行器，禁止进入正式
`harness.cli task-run`；正式入口仍固定使用真实模型适配器与 Docker Sandbox。

`coverage.json` 将每个 G-ID 绑定到 `unittest discover` 必跑的真实基础设施测试，发布
门禁会检查映射目标确实存在，避免只有用例描述而没有可执行系统断言。

P0 用例覆盖越权、未授权工具、超时、取消、恢复幂等、不确定副作用、虚假完成和
Sandbox 不可用，任何 P0 回归都必须阻断发布。
