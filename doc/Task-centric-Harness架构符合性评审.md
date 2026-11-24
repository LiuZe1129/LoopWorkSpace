# Task-centric Harness 架构符合性评审

> 评审日期：2026-11-08  
> 评审对象：SPEC-GraphFlow 当前项目实现  
> 架构基准：`doc/以Task为基准的Harness架构设计.md` v1.0  
> 评审方式：Intended vs. Implemented（以文档意图对照代码强制点）  
> 评审性质：架构符合性评审，不包含代码修改

## 1. 执行摘要

当前项目具备较成熟的 SPEC、GraphFlow、Artifact、Evidence 外围能力，但尚未实现架构基准定义的 Task-centric Harness。

当前系统更准确的定位是：

> 确定性 GraphFlow 运行时 + 可插拔 NodeHandler 接口 + Harness 前置基础设施。

项目已经拥有 Published SpecSnapshot、ExecutionPlan、DRG、ToolContract、ContextPack、事件日志、重试、ConformanceReport 和 EvidenceManifest 等能力。这些资产可以直接复用于 Harness 建设，不建议推倒重来。

主要问题是建设顺序倒置：GraphFlow 编排壳层已经较完整，但 Harness Phase 1 所要求的“单 Task + 单 Agent + 单 Sandbox”尚未建立。当前缺少独立 TaskExecution、真实 Agent Loop、LLM Gateway、Tool Router、运行时 Policy Engine、Sandbox 生命周期和标准 TaskResult。

此外，当前验收命令可以通过 `shell=True` 直接在宿主仓库根目录执行。这与基准中“高风险 Tool 必须经过 Policy 与 Sandbox”的要求存在直接冲突，应作为最高优先级问题处理。

## 2. 评审范围与方法

### 2.1 评审基准

本次评审以 `doc/以Task为基准的Harness架构设计.md` 为唯一 Harness 架构基准，重点检查以下不可妥协原则：

1. Task 定义执行边界，Execution 定义一次运行实例。
2. AgentProfile 提供能力偏好，不授予权限。
3. Harness 管理 Task 内动态执行，GraphFlow 管理 Task 间编排。
4. Agent Loop 是 Harness 核心，但不等于 Harness 全部。
5. 所有 Tool Call 必须经过 Tool Router 与 Policy Engine。
6. 高风险系统执行必须通过可销毁 Sandbox 隔离。
7. Artifact 与 Evidence 必须持久化。
8. 任务成功必须由客观验收证据确认。
9. 超时、取消、重试、恢复必须具有确定语义和审计记录。
10. 先建立单 Task 运行时，再扩展分布式和复杂多 Agent。

### 2.2 实现证据范围

评审检查了：

- `scheduler/` 下的 GraphRun、DRG、Executor、Retry、ToolContract、Gate、Evidence 和 Conformance 实现；
- `agents/` 下的 Agent 定义与 Schema；
- `.loop/runs/` 中的实际运行状态、事件、ContextPack 和 Artifact；
- `specs/dahua-client/` 中的 ProductSpec、ChangeSpec 和 Published Snapshot；
- `tests/test_scheduler_runtime.py` 中的运行时测试。

### 2.3 证据原则

- 文档声明不等于实现；必须存在实际代码强制点。
- Tool 或权限名称不等于安全边界；必须检查调用路径是否真正经过授权。
- Artifact 存在不等于需求已实现；必须检查 Artifact 是否包含客观结果。
- 未发现实现证据的能力标记为缺失或待验证，不根据名称推断。

## 3. 总体符合性矩阵

| 基准能力 | 当前实现证据 | 评审结论 |
| --- | --- | --- |
| SPEC/Snapshot 不可变合同 | `scheduler/spec_flow.py`、`scheduler/specs.py` | 符合 |
| GraphFlow DAG 编排 | `scheduler/plans.py`、`scheduler/drg.py`、`scheduler/executor.py` | 符合 |
| TaskSpec | WorkPackage、Graph Node、ContextPack 可部分映射 | 部分符合 |
| TaskExecution 独立实体 | 无 execution_id、attempt、iteration、sandbox_id | 缺失 |
| AgentProfile | Agent Markdown + 静态 ToolContract | 部分符合 |
| Agent Loop | 无 LLM → Tool → Observation 多轮循环 | 缺失 |
| Context Manager | 只有一次性 ContextPack | 部分符合 |
| LLM Gateway | 未发现实现 | 缺失 |
| Tool Router | 未发现统一 ToolCall Schema 和分发层 | 缺失 |
| Policy Engine | 静态 ToolContract 仅校验节点和 Artifact | 严重不足 |
| Sandbox Runtime | 未发现 SandboxRuntime、DockerRuntime、SandboxManager | 缺失 |
| TaskResult | 只有 NodeExecutionResult 和 ExecutorResult | 缺失 |
| Artifact/Evidence | Artifact、Conformance、EvidenceManifest 已落地 | 符合 |
| Task/Agent Loop 预算 | 只有 GraphRun 墙钟超时和节点重试次数 | 部分符合 |
| Tool/Sandbox 预算 | 无 CPU、内存、PID、日志、网络限制 | 缺失 |
| 取消与资源清理 | 无 CANCELLED、Sandbox 清理和 Reconciler | 缺失 |
| 审计日志 | 节点级事件较完整，ToolCall 级字段缺失 | 部分符合 |
| 人工审批暂停/恢复 | AWAITING_HUMAN 与恢复路径已实现 | 部分符合 |

## 4. 关键发现

### 4.1 P0：验收命令直接在宿主工作区执行

#### 架构意图

基准要求 Shell、Python、构建和测试等高风险操作在经过 Tool Router 与 Policy Engine 后，由 Sandbox Worker 在隔离环境执行。所有 Tool Call 都不能绕过授权。

#### 当前实现

`scheduler/gate_auto.py:28-30` 从 Published SpecSnapshot 的 `acceptance_criteria[].check` 构造验收项，并调用 `run_check()`。

`scheduler/gates.py:40-50` 使用：

```python
subprocess.run(
    item.check,
    cwd=root,
    shell=True,
    ...
)
```

命令直接在仓库根目录和宿主进程权限下执行，没有经过 Tool Router、运行时 Policy Decision 或 Sandbox。

#### 风险

- 潜在攻击者：恶意或被污染的 Spec 作者、上游 Agent、需求输入链路。
- 受影响对象：宿主工作区、源码、环境变量、凭据以及宿主可访问的网络资源。
- 影响：任意命令执行、源码或 Artifact 篡改、凭据泄露、后台进程残留。

#### 修复建议

1. 禁止生产路径直接调用 `subprocess.run(..., shell=True)`。
2. 将所有验收命令转换为标准 ToolCall。
3. 执行路径统一改为：

```text
Gate/Test Handler
  → ToolRouter
  → PolicyEngine
  → SandboxRuntime.execute()
  → ToolResult
```

4. 在 Sandbox 接入前，可使用显式标记的 `LocalTestRuntime` 支持开发测试，但不得作为生产安全实现。

### 4.2 P0：没有独立 TaskExecution

#### 架构意图

一个逻辑 Task 可以拥有多次 Execution。每次重试必须创建新的 `execution_id`，避免失败状态、日志、预算和副作用与新尝试混淆。

#### 当前实现

`scheduler/runs.py:71-94` 的 `RunState` 只有：

- run_id；
- Graph 节点状态；
- retry_count；
- Artifact 引用；
- last_error。

缺少：

- task_id；
- execution_id；
- attempt；
- iteration；
- sandbox_id；
- checkpoint_ref；
- token_usage；
- tool_call_count；
- started_at / finished_at。

`scheduler/retry.py:27-89` 在同一 RunState 中删除节点和下游结果、增加 retry_count 后继续运行，没有创建新的 Execution。

#### 影响

- 无法区分不同尝试的预算、日志和副作用。
- 无法安全恢复外部 API 写入或部署操作。
- 失败 Artifact 被删除后，审计链可能不完整。
- 无法表达 Execution 迁移到新 Sandbox 的情况。

#### 修复建议

建立三层模型：

```text
GraphRun
  └─ TaskSpec / task_id
       ├─ TaskExecution exec-001 / attempt=1
       └─ TaskExecution exec-002 / attempt=2
```

GraphFlow 的节点重试应创建新的 TaskExecution；旧 Execution 保持终态并保留日志和 Evidence。

### 4.3 P0：Agent Loop、LLM Gateway、Tool Router 未实现

#### 架构意图

Harness 应在单 Task 内驱动多轮：

```text
LLM → ToolCall → Policy → ToolResult → Observation → LLM
```

#### 当前实现

`scheduler/executor.py:342-379` 按 node ID 或 agent 名选择一次 NodeHandler。未注册真实 Handler 时，会写一个确定性成功 Artifact 并返回成功。

`scheduler/agent_invoker.py:19-71` 只生成 ContextPack，没有调用 Agent，也没有 LLM Gateway、ToolCall 或多轮上下文处理。

#### 影响

- 当前无法执行真实 AI 开发任务。
- Agent 无法根据 ToolResult 继续推理和修复。
- 没有模型调用重试、Token 计量、上下文裁剪和停止条件。
- 模块名称 `agent_invoker.py` 容易使使用者误判已存在 Agent 调用能力。

#### 修复建议

- 将当前文件重命名或拆分为 `context_pack_builder.py`。
- 新增 `HarnessRuntime` 与 `HarnessClient`。
- GraphFlow 通过 `HarnessClient.submit(TaskSpec)` 调用 Harness。
- Harness 内部实现 LLM Gateway、Agent Loop、Context Manager 和 Tool Router。

### 4.4 P0：确定性占位执行可能产生假成功

#### 当前实现

- `scheduler/executor.py:368-379` 为普通节点生成确定性成功结果。
- `scheduler/executor.py:439-463` 的默认 Review Handler 固定返回 APPROVE。
- Deploy 节点写入确定性部署结果。
- `scheduler/conformance.py:74-82` 在实现节点已完成且 Artifact 文件存在时，将 Requirement 标记为 PASS。
- `scheduler/conformance.py:111-119` 对测试节点也主要根据节点完成和结果文件存在判断。

#### 架构偏差

基准要求成功必须由客观验收 Evidence 确认，不能仅根据模型或 Handler 自称完成。

当前实现中，存在一个 JSON 结果文件可能被提升为“需求已经实现”。

#### 修复建议

- 确定性 fallback 只能返回 `SIMULATED` 或 `UNVERIFIED`。
- 禁止模拟 implementation/review/deploy 节点推动生产 Run 到 DONE。
- 实现任务至少收集 Git Diff、变更文件哈希或源码 Artifact。
- 测试结果必须包含 Sandbox 内命令、退出码、报告和运行环境标识。
- Review 必须基于实际 Diff、Requirement 和测试 Evidence。

### 4.5 P1：ToolContract 不是运行时 Policy Engine

#### 当前能力

`scheduler/tool_contracts.py:54-127` 为不同 Agent 定义静态：

- allowed_tools；
- read_paths；
- write_paths；
- artifact_types；
- node_types。

`scheduler/tool_contracts.py:151-189` 校验 Agent 与节点类型是否匹配。

`scheduler/tool_contracts.py:192-230` 及后续代码校验 Handler 返回的 Artifact 类型和路径。

#### 缺失能力

- TaskSpec 级 allowed_tools 上限；
- 调用者身份和资源访问权限；
- Tool 参数 Schema；
- 路径规范化后的实时授权；
- 网络目标限制；
- 高风险副作用审批；
- 每次调用的 PolicyDecision；
- ToolCall 与 ToolResult 审计 ID；
- Task、Agent、平台策略的权限交集。

#### 修复建议

现有 ToolContract 可保留，但应定位为 AgentProfile 默认能力或 Policy 输入。实际权限应按以下交集冻结到 TaskExecution：

```text
TaskSpec.allowed_tools
∩ AgentProfile.tool_preferences
∩ 调用者权限
∩ 平台策略
∩ 当前审批状态
```

### 4.6 P1：GraphFlow 与 Harness 没有真正解耦

#### 架构意图

- GraphFlow 负责哪个 Task 何时执行、依赖是否满足。
- Harness 负责当前 Task 如何被 Agent 完成。

#### 当前实现

`DRGExecutor` 同时承担：

- DAG 调度；
- ContextPack 生成；
- Handler 调用；
- 验收 Shell 执行；
- Review/Deploy 模拟；
- 重试；
- Evidence 收口。

#### 修复建议

将节点执行边界调整为：

```text
DRGExecutor
  → TaskSpecFactory
  → HarnessClient.submit(TaskSpec)
  ← TaskResult
  → GraphFlow Gate / 后继节点
```

DRGExecutor 不应理解 LLM 轮次、ToolCall、Conversation History 或 Sandbox 生命周期。

### 4.7 P1：缺少标准 TaskResult

`scheduler/executor.py:27-41` 的 `NodeExecutionResult` 和 `ExecutorResult` 不能替代基准中的 TaskResult。

缺失字段包括：

- task_id；
- execution_id；
- attempt；
- 标准终态；
- 验收结论；
- Evidence 汇总；
- Token/Tool/时长使用量；
- 错误分类；
- Sandbox 清理状态。

建议新增独立且版本化的 TaskResult。GraphFlow 应只消费 TaskResult，不直接解释 Harness 内部状态文件。

### 4.8 P1：Task 生命周期与预算不完整

当前状态是 GraphRun 状态，不是 TaskExecution 状态。缺少：

- INITIALIZING；
- VALIDATING；
- SUCCEEDED；
- CANCELLED；
- TIMED_OUT。

当前具有墙钟超时和节点重试，但缺少：

- max_iterations；
- Token 和费用预算；
- max_tool_calls；
- Tool 单次执行预算；
- CPU、内存、PID 和日志限制；
- 取消传播；
- 超时后的进程或容器终止确认。

### 4.9 P1：可观测性只到节点级

当前 EventLogger 可以记录 Run 和节点事件，属于可复用基础。但基准要求的以下字段尚未形成统一日志：

```text
trace_id
task_id
execution_id
iteration
tool_call_id
sandbox_id
policy_decision
latency_ms
token_usage
error_code
```

在 Harness 接入后，应将 Node Event 扩展为 TaskExecution 和 ToolCall 两级事件模型。

## 5. 已符合或可复用能力

以下现有资产与新基准兼容，应通过适配复用：

### 5.1 Spec 与执行绑定

- ProductSpec、ChangeSpec、Published SpecSnapshot 分层明确。
- Snapshot 具有 content hash 和不可变发布语义。
- GraphRun 与 Snapshot 绑定关系可审计。

### 5.2 GraphFlow 编排

- ExecutionPlan 和 DRG 已实现。
- 节点依赖和 DAG 校验已实现。
- Requirement/Acceptance 覆盖校验已实现。
- 支持暂停、恢复、重试、Spec 修订和旧 Run supersede。

### 5.3 ContextPack

`scheduler/agent_invoker.py` 已能生成：

- bound_spec；
- node 信息；
- ToolContract；
-输入 Artifact 引用；
- 依赖 Artifact 引用。

该结构可直接作为 Context Manager 的初始输入包。

### 5.4 Artifact、Evidence 与追踪

- Artifact 路径引用模式符合跨 Task 传递思路。
- EvidenceManifest 包含文件哈希和大小。
- ConformanceReport 具有 Requirement → Acceptance → Node → Evidence 追踪矩阵。
- 逐节点状态快照可以演进为 TaskExecution Checkpoint。

### 5.5 Retry 与人工审核

- RetryPolicy 已支持节点、类型和失败原因优先级。
- AWAITING_HUMAN 和人工恢复路径可以迁移到 TaskExecution WAITING_APPROVAL。

## 6. 现有资产到 Harness 模块的映射

| 现有能力 | Harness 中的建议位置 |
| --- | --- |
| WorkPackage / Graph Node | TaskSpec 来源 |
| ContextPack | Context Manager 初始输入 |
| Agent Markdown | AgentProfile 来源 |
| ToolContract | 默认能力配置 / Policy 输入 |
| Node Artifact | TaskResult.artifacts |
| Gate/Test Result | TaskResult.evidence |
| State Snapshot | TaskExecution Checkpoint |
| EventLogger | Task/Tool 审计日志基础 |
| RetryPolicy | GraphFlow 重试策略 + Harness attempt 策略输入 |
| ConformanceReport | GraphFlow 流程级验收与追踪 |
| EvidenceManifest | Workflow 级证据归档 |

## 7. 推荐目标架构

```text
Published SpecSnapshot
        ↓
ExecutionPlan / DRG
        ↓
GraphFlow DRGExecutor
        ↓ TaskSpec + Input Artifact Refs
HarnessClient
        ↓
Task Controller ───── ExecutionRepository
        ↓
Context Manager ───── LLM Gateway
        ↓                    ↑
Agent Loop ───────── ToolResult / Observation
        ↓ ToolCall
Tool Router
        ↓
Policy Engine
   ├─ Sandbox Tool → Sandbox Manager → DockerRuntime
   └─ Controlled API Tool → Resource Authorization / Approval
        ↓
Result Manager
        ↓ TaskResult + Artifact + Evidence
GraphFlow Gate / 后继节点
```

## 8. 推荐工程目录

```text
graphflow/
  scheduler.py
  task_graph.py
  task_spec_factory.py
  harness_client.py

harness/
  models.py
  task_controller.py
  agent_loop.py
  context_manager.py
  llm_gateway.py
  tool_router.py
  result_manager.py

policy/
  policy_engine.py
  policy_models.py

sandbox/
  runtime.py
  local_test_runtime.py
  docker_runtime.py
  sandbox_manager.py
  artifact_collector.py
  reconciler.py

tools/
  models.py
  registry.py
  read_file_tool.py
  write_file_tool.py
  shell_tool.py
  test_tool.py

storage/
  execution_repository.py
  artifact_store.py
```

现有 `scheduler/` 可以逐步迁移为 `graphflow/`，不要求一次性重构目录。

## 9. 实施路线

### 9.1 Phase 0：冻结核心契约

优先定义并测试：

1. TaskSpec；
2. TaskExecution；
3. ToolCall；
4. ToolResult；
5. TaskResult；
6. AgentProfile；
7. PolicyDecision。

同时明确：

- Graph Node 如何转换为 TaskSpec；
- TaskResult 如何映射回节点状态；
- GraphFlow retry 与 TaskExecution attempt 的关系；
- Artifact URI 和本地路径的转换规则。

### 9.2 Phase 1：单 Task Harness

完成最小真实链路：

```text
TaskSpec
→ Agent Loop
→ read_file
→ write_file
→ shell_exec in Sandbox
→ tests
→ Artifact/Evidence
→ TaskResult
```

验收要求：

- LLM 可以进行多轮 Tool 调用；
- 所有 ToolCall 有统一 ID 和 Schema；
- Tool Call 必须经过 PolicyDecision；
- Shell 和测试只能在 Sandbox 执行；
- 成功和失败均能保存 Evidence；
- Sandbox 最终被回收。

### 9.3 Phase 2：受控执行

补齐：

- Task、Agent、平台策略交集；
- 路径和网络目标限制；
- Token、Tool 次数、时长预算；
- CPU、内存、PID 和日志限制；
- 取消传播；
- Checkpoint；
- 孤儿 Sandbox Reconciler；
- 高风险操作人工审批。

### 9.4 Phase 3：接入 GraphFlow

将 implementation、test、review 等节点改为提交 TaskSpec。

GraphFlow 只处理：

- 依赖满足；
- Task 提交；
- TaskResult 接收；
- Gate；
- 流程级重试和失败分支；
- 跨 Task Artifact 传递。

### 9.5 Phase 4：安全与扩容

最后建设：

- 多 Worker 和租约；
- 资源配额；
- 网络代理和依赖代理；
- 凭据临时注入；
- MicroVM 或远程 Sandbox；
- 多租户隔离验证。

## 10. 建议验收场景

### 10.1 权限场景

- Task 未授权 shell_exec 时调用必须返回 denied。
- AgentProfile 声明 shell_exec，但 Task 未授权时仍必须拒绝。
- 尝试读取允许目录外文件时必须拒绝。
- 尝试访问未授权网络目标时必须拒绝。

### 10.2 Sandbox 场景

- 死循环受到墙钟和 CPU 限制。
- 子进程在超时后被实际终止。
- Task 结束后 Sandbox 被销毁。
- Worker 崩溃后 Reconciler 能回收孤儿 Sandbox。
- 不允许挂载 Docker Socket、宿主根目录和长期凭据。

### 10.3 Execution 场景

- 同一 Task 重试产生不同 execution_id。
- 旧 Execution 保留失败日志和 Evidence。
- 新 attempt 不继承已消耗预算。
- 从 Checkpoint 恢复时能够识别已有副作用。

### 10.4 结果场景

- 模型自称完成但测试失败时，TaskResult 不得为 succeeded。
- 只有占位 Artifact 时，状态必须是 UNVERIFIED/SIMULATED。
- TaskResult 中 Artifact 和 Evidence 引用必须可解析并可校验哈希。

### 10.5 GraphFlow 集成场景

- GraphFlow 不直接执行 Shell。
- GraphFlow 不读取 Harness 内部 Conversation History。
- GraphFlow 只依赖 TaskResult 推进节点。
- Task 失败后流程级重试创建新的 TaskExecution。

## 11. 测试现状说明

当前测试执行结果：

```text
Ran 123 tests
OK
```

这证明现有确定性 GraphFlow 运行时内部较稳定，覆盖：

- Spec 发布和校验；
- DRG 编译与执行；
- Gate 和 Acceptance Check；
- Retry、超时和熔断；
- 人工审核恢复；
- Spec 修订和 supersede；
- Design Spec 和 huaDesign 契约；
- Conformance 和 Evidence。

但这些测试主要验证现有架构自洽，不能证明 Task-centric Harness 基准已经满足。后续需要新增独立测试集：

```text
tests/harness/
tests/policy/
tests/sandbox/
tests/tools/
tests/integration/test_graphflow_harness.py
```

## 12. 最终结论

当前项目的 GraphFlow 外层成熟度较高，Harness 内核尚未达到 Phase 1。最需要优先处理的是宿主 Shell 直执行问题，以及 TaskExecution、Agent Loop、Tool Router、Policy Engine、Sandbox 和 TaskResult 六个核心缺口。

推荐采用渐进式迁移：

1. 保留现有 Spec、GraphFlow、ContextPack、Artifact、Evidence 和 Retry 能力；
2. 在 NodeHandler 边界后引入独立 Harness Runtime；
3. 先跑通单 Task、单 Agent、单 Sandbox；
4. 再把 implementation/test/review 节点逐步迁移到 TaskSpec/TaskResult 接口；
5. 最后建设分布式 Worker、多租户和更强隔离。

最终目标边界应稳定为：

```text
SPEC 定义目标、约束与验收
→ GraphFlow 编排 Task
→ Harness 执行单 Task
→ Tool Router / Policy 控制操作
→ Sandbox 隔离高风险执行
→ Artifact / Evidence 保存结果
→ TaskResult 驱动 GraphFlow 后续流程
```

