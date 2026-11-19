# SPEC-GraphFlow 向 Agent-Harness MVP 演进：当前缺陷评审

## 1. 评审结论

当前项目是一套具备较完整确定性编排外壳的 SPEC-GraphFlow 原型，但尚不是 Agent-Harness MVP。

依据《Harness Agent-Loop MVP 建设与评审基线》，本轮结论为：

> **不通过（D）：当前适合继续作为 GraphFlow 编排与治理基础，不具备真实 Agent-Harness MVP 的发布条件。**

主要原因不是 GraphFlow 不完整，而是 Harness 内核尚未建立：没有真实 Agent Loop、Model Gateway、Tool Router、运行时 Policy、Sandbox、TaskExecution/Attempt 和 Golden Case 评测闭环。当前默认执行器还能在没有真实 Agent 的情况下生成“成功”产物并推进至 DONE。

### 1.1 模块评级

| MVP 模块 | 评级 | 当前状态 |
|---|---:|---|
| Agent Runtime / Loop Engine | D | 有 DRG 节点循环，没有 LLM → Tool → Observation Agent Loop |
| Run State / Event / Checkpoint | C | 有文件持久化、事件和状态快照，缺少原子性、并发控制、Attempt 和副作用恢复 |
| Model Gateway | D | 未实现 |
| Tool Registry / Tool Executor | D | 有静态 ToolContract，没有统一 ToolCall/ToolResult 和真实受控执行边界 |
| Context Builder | C | 有 ContextPack 引用组装，没有消息历史、Token 预算、裁剪和模型上下文构建 |
| Policy / Approval | D | ToolContract 不执行逐次授权；Shell 可绕过；人工审核没有身份授权 |
| Artifact / Versioned Configuration | C | Artifact/Evidence 较完整，未固定 Agent、Prompt、模型、Tool、Policy 版本 |
| Observability / Replay | C | 有节点事件和状态快照，缺少模型、ToolCall、Policy、成本和可重放语义 |
| Evaluation Harness | C | 123 个 GraphFlow 测试通过，但没有版本化 Golden Cases 与框架对比能力 |

### 1.2 已触发的硬门槛

对应 MVP 基线第 6 节：

| 硬门槛 | 结论 | 说明 |
|---|---|---|
| #2 无法限制最大 Step、执行时间或资源预算 | 触发 | 墙钟超时默认为 `None`，CLI 无超时参数；没有 Token、费用、Tool 次数和 Sandbox 资源预算 |
| #3 工具执行可绕过 Policy | 触发 | Spec 中的验收命令直接进入 `subprocess.run(..., shell=True)` |
| #4 高风险副作用缺少硬性审批 | 触发 | Shell、测试和模拟部署路径未经过风险分级及审批门 |
| #5 重试可能重复不可逆副作用 | 触发 | 没有 ToolCall 幂等键、执行账本或副作用确认机制 |
| #8 Prompt、模型、工具或 Agent 配置不可确定版本 | 触发 | Run 仅绑定 Spec；未固化 Prompt/Agent/Model/Policy/Tool 版本或哈希 |
| #9 没有自动化 Golden Cases | 触发 | 当前测试验证 GraphFlow 代码行为，不是可配置、可对比的 Agent 任务评测集 |

## 2. 已验证的可复用资产

以下能力不建议推倒重来：

- Published SpecSnapshot 与内容哈希；
- ExecutionPlan 和 DRG 编译；
- GraphRun 状态、节点依赖和有界重试；
- JSONL Event 基础；
- State Snapshot 基础；
- Artifact、EvidenceManifest 和 ConformanceReport；
- ToolContract 的静态能力声明；
- AWAITING_HUMAN / AWAITING_SPEC_CHANGE 暂停与恢复路径；
- Spec 修订、审批、重新绑定和 supersede；
- 123 个现有单元/集成测试。

现有 `doc/graphflow-runtime.md:280` 也明确说明当前执行器不会调用 LLM Agent、修改代码、运行真实测试或部署系统。这与代码事实一致。

## 3. P0 缺陷

### GAP-P0-01：不存在真实 Agent Loop

**基线意图**：Runtime 必须支持模型决策、Tool Call、Observation 和下一轮模型决策，并具有停止条件和预算。

**实现证据**：

- `scheduler/agent_invoker.py:19-71` 只生成 ContextPack 文件；
- `scheduler/executor.py:342-379` 仅调用注入的 Python handler；
- 未找到 LLM Provider、Model Gateway、消息历史、Tool Call 或 Observation 实现；
- `doc/graphflow-runtime.md:280` 明确承认当前不是完整 Agent executor。

**影响**：系统无法完成真实自主工程任务，也无法验证多轮工具使用、上下文管理、Token 预算和模型失败恢复。

**MVP 修复条件**：新增独立 Harness Runtime，由 GraphFlow 节点提交一个 TaskExecution；Harness 内执行单 Agent 多轮 Loop，最终只向 GraphFlow 返回标准 TaskResult。

### GAP-P0-02：未处理的 Agent 节点会被直接标记成功

**基线意图**：任务成功必须由真实执行结果和客观验收证据确定。

**实现证据**：

- `scheduler/executor.py:342-379`：节点没有 handler 时调用 `write_node_result()` 并返回 `success`；
- `scheduler/executor.py:747-760`：产物声明节点由 deterministic executor 完成；
- `scheduler/executor.py:439-463`：Review fallback 永远返回 `APPROVE`；
- `scheduler/executor.py:465-480`：Deploy fallback 永远返回 `success=True`。

**影响**：Smoke flow 可以在未写代码、未真实评审、未真实部署的情况下进入 DONE。DONE 当前证明的是图可以走通，不是工程任务完成。

**MVP 修复条件**：生产/验收模式下，缺失 handler 必须 fail-closed；Mock/fallback 只能在显式测试模式启用，并在 Run 和 Artifact 上标记 `execution_mode=mock`，禁止作为发布证据。

### GAP-P0-03：Spec 验收命令可在宿主仓库直接执行

**基线意图**：所有高风险工具必须经过 Tool Router、Policy 和隔离执行环境。

**实现证据**：

- `scheduler/gate_auto.py:22-30` 从 SpecSnapshot 的 acceptance criteria 读取命令；
- `scheduler/gate_auto.py:83-105` 将 `check` 原样转换为 DoneItem；
- `scheduler/gates.py:40-50` 使用 `subprocess.run(item.check, cwd=root, shell=True)`；
- `scheduler/specs.py:245-248` 只检查 `check/expect` 非空，不限制命令内容。

**攻击路径**：被污染的 Spec、上游 Agent 输出或错误配置可以把任意 Shell 命令带入宿主工作区执行。

**影响**：可能读取密钥、篡改仓库、访问网络、启动残留进程，且 ToolContract 不会在该路径逐次授权。

**MVP 修复条件**：禁止业务路径直接调用 `shell=True`；验收命令必须转换为标准 ToolCall，经参数校验、PolicyDecision 和 Sandbox Worker 执行。

### GAP-P0-04：ToolContract 只是静态声明，不是运行时权限边界

**基线意图**：模型只能提出动作，Policy 决定动作是否允许，Executor 才能执行。

**实现证据**：

- `scheduler/tool_contracts.py:54-127` 定义 Agent 的工具名和路径模式；
- `scheduler/tool_contracts.py:151-189` 只校验 Agent、node type 和 Artifact 类型；
- 没有统一 ToolCall ID、参数 Schema、PolicyDecision、调用者身份、风险等级或审批状态；
- Shell gate 不经过 ToolContract 执行授权。

**影响**：配置中“允许哪些工具”并没有转化成强制执行点，无法阻止越权调用或审计一次具体工具操作。

**MVP 修复条件**：建设单一 Tool Router，所有 Agent 工具调用只能走该入口；权限取 Task 授权、Agent 能力、平台策略、审批状态和预算的交集。

### GAP-P0-05：没有 Sandbox 与可验证的资源终止

**基线意图**：Shell、代码、构建和测试在隔离环境执行，并限制 CPU、内存、PID、网络、磁盘、输出和时长。

**实现证据**：

- 当前 Shell 在宿主 `cwd=root` 执行；
- `subprocess.run` 只有等待超时，没有 Sandbox 生命周期；
- 没有进程树清理、容器回收、网络策略或孤儿任务 Reconciler。

**影响**：超时不等于所有子进程均被终止；并发任务也无法保证工作区、进程和数据隔离。

**MVP 修复条件**：先实现单 Task、单 Agent、单 Sandbox；提供创建、执行、终止、销毁和异常回收的最小生命周期。

### GAP-P0-06：Run 没有强制总预算和取消语义

**基线意图**：每个 Run 必须受最大轮次、总时长、Token、费用和 Tool 次数限制，并支持取消。

**实现证据**：

- `scheduler/executor.py:51-59` 的墙钟超时可选且默认 `None`；
- `scheduler/main.py:93-96` 的 execute CLI 只有 `--max-steps`，没有总超时、Token、费用或 Tool 次数参数；
- Run 状态集中没有 `CANCELLED`、`TIMEOUT`；超时被写成 `FAILED`；
- 没有 cancel 命令和取消向工具/子进程传播的机制。

**影响**：正式入口不能保证任务资源上限，也无法可靠停止正在执行的任务。

**MVP 修复条件**：预算成为 TaskExecution 必填配置；每轮模型调用和每次 Tool Call 前后原子计量，取消信号传播至模型请求、Tool Executor 和 Sandbox。

## 4. P1 缺陷

### GAP-P1-01：缺少 TaskExecution 与 Attempt 模型

GraphRun 同时承担业务 Run、执行尝试和状态容器。`scheduler/retry.py:43-65` 在同一 Run 上回退节点、增加 retry_count，并删除下游产物。

缺少：

- 独立 execution_id / attempt_id；
- 每次尝试的预算、模型、上下文和 Sandbox；
- 失败尝试的不可变结果；
- 外部副作用账本及幂等键。

建议保留 GraphRun 作为 GraphFlow 编排实例，在其下新增 Task、TaskExecution 和 Attempt。

### GAP-P1-02：文件状态存储缺乏原子性与并发控制

**实现证据**：

- `scheduler/runs.py:219-223` 和 `286-291` 直接覆盖 JSON 文件；
- `scheduler/events.py:31-40` 直接追加 JSONL；
- 没有临时文件原子替换、版本号、锁、租约或 compare-and-swap；
- 状态、Artifact 和 Event 分多次写入，崩溃后可能出现部分提交。

**影响**：并发执行、重复 worker 或异常退出可能造成状态丢失、JSON 损坏、重复节点和时间线不一致。

**MVP 修复条件**：单机 MVP 至少实现原子文件替换、Run 锁/租约和 state version；后续可迁移到事务数据库而不改变领域接口。

### GAP-P1-03：run_id 和 Artifact 路径边界未统一强制

`scheduler/runs.py:103-104` 直接拼接 `runs_dir / run_id`，CLI 又允许传入自定义 run_id。没有统一校验 run_id 或验证 resolve 后仍位于 `.loop/runs`。

**影响**：进入服务化或接收不可信输入后，可能形成目录穿越和越界写入风险。

**MVP 修复条件**：统一 ID 格式；所有路径通过安全解析器生成，并验证解析后的绝对路径位于授权根目录内。

### GAP-P1-04：ContextPack 不是完整 Context Manager

`scheduler/agent_invoker.py:44-67` 只写入 Spec、Run、Plan、Graph、Contract 和 Artifact 引用。

缺少：

- System Prompt 和 Agent Profile 的版本化装配；
- 消息历史和 Tool Observation；
- Token 预算及优先级；
- 裁剪、摘要和来源追踪；
- 上下文级权限和敏感数据过滤。

### GAP-P1-05：审批只是状态切换，没有鉴权与请求对象

`scheduler/main.py:111-114` 允许直接通过 CLI APPROVE/REJECT；`scheduler/executor.py:910-954` 记录 decision 和 reason，但没有 approval_id、审批人身份、角色、授权范围、过期时间或参数绑定。

Spec revision 的 `approved_by` 也是调用方提供的自由字符串，不能构成身份验证。

**影响**：任何能够运行 CLI 的调用方都可恢复待审任务；审批也未绑定到某个具体高风险 ToolCall。

### GAP-P1-06：可观测性停留在 Node 层

已有 Event 和 Snapshot 是良好基础，但缺少：

- LLM request/response 引用；
- ToolCall/ToolResult ID、参数摘要和结果；
- PolicyDecision；
- Token、费用和 Tool 次数；
- event_id、sequence、causation_id；
- 可重放而不重复副作用的执行账本。

当前 `events` CLI 可查看历史，但不能完成 Harness 级 Replay。

### GAP-P1-07：配置没有形成可复现版本闭包

RunState 在 `scheduler/runs.py:70-95` 主要保存 Spec 和节点状态，没有记录：

- Agent Profile 版本或哈希；
- System Prompt 版本；
- 模型供应商、模型 ID 和参数；
- Tool 实现版本；
- Policy 版本；
- Harness Runtime 版本。

因此一次运行无法被严格复现或与另一版本公平比较。

### GAP-P1-08：现有测试不是 Evaluation Harness

本轮执行 `python -m unittest discover -s tests -p 'test_*.py' -v`，结果为 123/123 通过。

这些测试主要覆盖 Spec、Plan、DRG、状态、重试、Gate 和 Evidence，价值较高，但缺少：

- 独立 Golden Case 数据格式；
- 模型/Prompt/Policy/Tool 版本记录；
- Agent 任务级成功、成本和延迟指标；
- 越权 Tool、Prompt Injection、Sandbox 逃逸和副作用幂等场景；
- 新旧版本批量对比；
- CI 配置和发布门禁。

测试中还出现两处未关闭文件的 `ResourceWarning`，不影响本次通过结论，但应清理。

## 5. P2 结构与文档缺陷

### GAP-P2-01：逻辑架构文档与实现状态漂移

`doc/architecture.md:418-422` 将 Agent、质量保障、DevOps 和运行时重构标记为已完成；`doc/architecture.md:456-479` 描述了真实 LLM 会话和 Agent 执行。

但 `doc/graphflow-runtime.md:280` 和代码明确表明这些能力尚未实现。评审时应以运行时代码和后者的边界声明为准。

### GAP-P2-02：GraphFlow 与 Legacy TaskCard 双状态机并存

`scheduler/runs.py` 和 `scheduler/state.py` 分别维护 GraphRun 与旧 TaskCard 状态机。虽然注释说明二者独立，但继续并行演进会增加转换、审批、重试和终态语义冲突。

MVP 改造时应明确：GraphFlow 是外层编排，Harness TaskExecution 是内层执行；Legacy TaskCard 进入只读兼容或迁移路径。

### GAP-P2-03：缺少工程化包装与持续集成入口

当前未发现 `pyproject.toml`、依赖锁定文件、Dockerfile 或 CI 工作流。CLI 原型可以运行，但安装、环境复现、静态检查、测试门禁和发布产物尚未标准化。

## 6. 推荐的 MVP 改造顺序

### 阶段 0：先关闭危险路径

1. 禁止正式模式下的 deterministic success fallback；
2. 隔离或暂时禁用来自 Spec 的任意宿主 Shell；
3. 为 Run ID 和文件路径增加根目录边界；
4. 给当前执行入口增加强制总超时和取消状态。

### 阶段 1：建立最小 Harness 内核

1. 定义 Task、TaskExecution、Attempt、AgentProfile 和 TaskResult；
2. 实现 Model Gateway；
3. 实现单 Agent Loop；
4. 实现统一 ToolCall/ToolResult；
5. 实现 Tool Router 和 PolicyDecision；
6. 实现单 Task 单 Sandbox；
7. 实现预算、Checkpoint、取消和标准终态。

### 阶段 2：接回现有 GraphFlow

1. DRGExecutor 的 implementation/test/review 节点提交 Harness Task；
2. Harness 返回 TaskResult、Artifact 和 Evidence；
3. GraphFlow 只根据标准结果推进、重试、请求 SpecChange 或升级人工；
4. deterministic handler 仅保留给单元测试。

### 阶段 3：补齐评测与发布门禁

1. 建立基线要求中的最小 Golden Cases；
2. 覆盖越权、超时、取消、恢复、幂等和 Sandbox 场景；
3. 记录模型、Prompt、Tool、Policy 和 Runtime 版本；
4. 接入 CI，形成 MVP 自动评审报告。

## 7. MVP 改造的首个纵向切片

建议第一个真正可验收的切片不是重写整套 GraphFlow，而是：

```text
一个 implementation DRG Node
→ 创建 TaskExecution
→ 单 Agent 调用 Model Gateway
→ read_file / write_file / shell_test ToolCall
→ Tool Router + PolicyDecision
→ 单 Sandbox 执行
→ 多轮 Observation
→ 确定性测试验收
→ TaskResult + Artifact + Evidence
→ GraphFlow 节点完成
```

该切片成功后，现有 Plan、DRG、Retry、Artifact、Evidence 和 Conformance 能够继续复用，同时 Harness 的关键边界也能被端到端验证。

## 8. 评审证据

- 目标基线：`documentation/agent-loop-mvp-review-baseline.md`；
- 主要实现：`scheduler/`；
- Agent 声明：`agents/`；
- 当前运行时边界：`doc/graphflow-runtime.md:278-288`；
- 测试命令：`python -m unittest discover -s tests -p 'test_*.py' -v`；
- 测试结果：123 个测试通过，2 个 ResourceWarning；
- 本轮未修改生产代码。

