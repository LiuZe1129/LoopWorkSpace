# 项目路径树与 Agent-Harness 文件定位

本文档是当前项目的路径级架构索引，用于回答三个问题：

1. 一个文件属于 GraphFlow 控制面、Harness 执行面，还是规格/验证支撑面；
2. 它位于一次 Agent Task 执行流程的哪个环节；
3. 修改该文件时会影响哪些上游输入、下游状态或安全边界。

标记规则：

- `★ 核心`：直接决定正式 Harness 执行、权限、状态、隔离、恢复或验收行为；
- `桥接`：连接 GraphFlow 与 Harness，两侧契约都可能受影响；
- `支撑`：提供规格、文档、测试、门禁或示例数据；
- `遗留`：仍被兼容流程使用，但不应成为新增 Harness 能力的落点。

> 中文注释要求：新增或修改 `harness/` 公共模块、类和函数时，必须使用中文说明“当前功能”和“位于 Harness 哪个架构层或执行流程环节”。该要求由 `scripts/check_harness_comments.py` 自动检查。

## 1. 总体架构定位

```text
用户 / CLI
    │
    ▼
scheduler/                         GraphFlow 控制面
  Spec → Plan → DRG → Node 调度
                    │
                    ▼
  integration/graphflow_handler.py  控制面/执行面桥接
                    │ Task
                    ▼
harness/                           Harness 单 Task 执行面
  RuntimeFactory → TaskRunner → AgentLoop
      │              │          ├─ Model Gateway
      │              │          └─ Tool Router → Policy → Sandbox
      │              ├─ Workspace Snapshot / ChangeSet
      │              ├─ Validator
      │              └─ Result / Event / Checkpoint
      ▼
TaskResult / Artifact / Evidence
                    │
                    ▼
scheduler/                         GraphFlow 汇总节点结果并推进图状态
```

架构边界：

- `scheduler/` 决定“接下来运行哪个 Task”，不直接执行模型、文件或 Shell；
- `harness/` 决定“一个 Task 如何安全完成”，不负责任务图拆解和跨 Task 编排；
- `harness/integration/graphflow_handler.py` 是两层之间唯一正式适配入口；
- `.loop/` 是运行时数据目录，不属于源代码树，也不能被 Agent 回写；
- `specs/`、`agents/` 是版本化输入；`tests/`、`scripts/` 是验证与发布门禁。

## 2. 项目路径树

```text
LoopWorkSpace/
├─ README.md                              # 项目入口、常用 Harness 命令
├─ pyproject.toml                         # Python 包、测试与 CLI 配置
├─ 00-UpdatePlan/                         # 历史/演进设计输入
├─ agents/                                # GraphFlow Agent 定义与输出约束
├─ specs/                                 # ChangeSpec、ProductSpec、发布快照
├─ scheduler/                             # GraphFlow 控制面
├─ harness/                               # 单 Task Agent-Harness 执行面
│  ├─ integration/                        # GraphFlow ↔ Harness 桥接
│  ├─ models/                             # 模型供应商隔离层
│  ├─ tools/                              # Tool 注册、授权、路由与副作用账本
│  ├─ sandbox/                            # 单 Attempt 隔离执行环境
│  ├─ workspace/                          # 本地源码快照、ChangeSet 与安全回写
│  ├─ persistence/                        # Task/状态/事件/租约/检查点/结果存储
│  └─ observability/                      # 时间线、指标和脱敏
├─ docker/                                # Sandbox 镜像定义
├─ scripts/                               # 中文注释与发布门禁
├─ tests/                                 # 阶段测试、Docker 冒烟、Golden Cases
├─ documentation/                         # 当前 Harness 评审与实施文档
├─ doc/                                   # 原 SPEC-GraphFlow 设计与历史评审文档
└─ shared/                                # Agent 协作期共享契约与黑板说明
```

## 3. 根目录与工程配置

| 文件 | 定位 | Harness 中的作用 |
|---|---|---|
| `README.md` | 支撑/入口 | 提供 Harness CLI、发布门禁和生产镜像摘要配置的最短使用路径。 |
| `pyproject.toml` | ★ 工程配置 | 声明包元数据、测试配置和 `loop-harness` 等入口；影响 Harness 如何安装、启动和测试。 |

## 4. `harness/`：单 Task 执行面

### 4.1 顶层执行内核

| 文件 | 定位 | 流程位置与职责 |
|---|---|---|
| `harness/__init__.py` | 执行面包入口 | 聚合公开领域契约，供 CLI、GraphFlow 适配层和测试稳定导入。 |
| `harness/contracts.py` | ★ 核心契约 | 定义 Task、WorkspaceConfig、TaskExecution、Attempt、TaskResult、预算与权限；位于所有模块之间的数据边界。 |
| `harness/errors.py` | ★ 错误边界 | 定义稳定错误分类；位于内部异常映射到 TaskResult、GraphFlow 和运维诊断的交界处。 |
| `harness/state_machine.py` | ★ 状态控制 | 约束 Execution/Attempt 合法迁移；位于任何状态落盘之前，防止跳过生命周期。 |
| `harness/controller.py` | ★ 生命周期控制器 | 创建 TaskExecution/Attempt，推进状态、提交 Checkpoint、取消/暂停/恢复并关联租约。 |
| `harness/runtime_factory.py` | ★ Composition Root | 正式装配 Model、Tool、Policy、Sandbox、Workspace、Validator、TaskRunner 和 GraphFlow Handler。 |
| `harness/task_runner.py` | ★ 端到端编排 | 单 Task 主入口；串联注册、租约、快照、Sandbox、AgentLoop、验收、ChangeSet、结果与异常清理。 |
| `harness/agent_loop.py` | ★ Agent 循环 | 执行模型推理、ToolCall、Observation、Checkpoint 和完成提案；位于 Attempt RUNNING 阶段。 |
| `harness/context_manager.py` | 模型上下文 | 将 Task、AgentProfile、历史消息与预算组织成模型请求，控制上下文窗口。 |
| `harness/budget.py` | ★ 资源边界 | 在下一轮模型、工具或验证前检查轮次、Token、调用次数、费用和墙钟时间。 |
| `harness/cancellation.py` | ★ 控制信号 | 在模型、工具和 Sandbox 之间传播协作式取消，位于长耗时操作的安全停止边界。 |
| `harness/validation.py` | ★ 成功门禁 | 对 Agent 完成声明执行确定性 acceptance；只有通过后才能进入成功和源码回写。 |
| `harness/schema_validation.py` | 契约校验 | 提供轻量 JSON Schema 校验，约束模型结构化输出及其他稳定载荷。 |
| `harness/recovery.py` | ★ 恢复判定 | 扫描非终态 Execution、租约、Ledger 和 Sandbox，区分可恢复与需人工对账。 |
| `harness/worker.py` | ★ Worker 基础 | 维持租约心跳并执行安全恢复；位于 Reconciler 判定与 TaskRunner.resume 之间。 |
| `harness/evaluation.py` | 评测 | 执行 Golden Cases、生成版本化评测报告并比较模型/Prompt/Policy/Runtime 版本。 |
| `harness/cli.py` | 运维入口 | 提供 Task 创建、运行、状态、事件、结果、取消、恢复、时间线和指标命令。 |

### 4.2 `harness/integration/`：GraphFlow 桥接层

| 文件 | 定位 | 流程位置与职责 |
|---|---|---|
| `harness/integration/__init__.py` | 桥接包入口 | 暴露版本化 AgentProfile 相关接口，避免 GraphFlow 依赖其内部存储细节。 |
| `harness/integration/agent_profile.py` | ★ Agent 身份版本 | 将 Prompt、模型、工具能力和输出 Schema 固化为带哈希的不可变 AgentProfile。 |
| `harness/integration/graphflow_handler.py` | ★ 桥接 | 将 GraphFlow Node 转换为 Task，解析 AgentProfile，调用 TaskRunner，再把 TaskResult 映射回 NodeExecutionResult。 |

### 4.3 `harness/models/`：模型供应商隔离层

| 文件 | 定位 | 流程位置与职责 |
|---|---|---|
| `harness/models/__init__.py` | 模型层入口 | 暴露稳定模型契约、Gateway 和正式 Provider Adapter。 |
| `harness/models/base.py` | ★ 模型契约 | 定义 ModelRequest、ModelResponse、Usage、Provider 和标准 ModelError。 |
| `harness/models/gateway.py` | ★ 模型网关 | 统一超时、有限重试、取消、结构化输出校验和用量汇总。 |
| `harness/models/provider_adapter.py` | 外部边界 | 将稳定 ModelRequest 适配到 OpenAI Responses API；API Key 只停留在服务端请求头。 |

### 4.4 `harness/tools/`：工具授权与执行面

| 文件 | 定位 | 流程位置与职责 |
|---|---|---|
| `harness/tools/__init__.py` | Tool 层入口 | 汇总 Registry、Policy、Router 和 MVP 工具构建函数。 |
| `harness/tools/contracts.py` | ★ Tool 契约 | 定义 ToolDefinition、ToolCall、ToolResult 与执行上下文，是 Agent 和执行器之间的硬边界。 |
| `harness/tools/registry.py` | ★ 工具注册表 | 保存允许进入平台的工具定义与实现，拒绝未知或重复工具。 |
| `harness/tools/policy.py` | ★ 权限门禁 | 计算 Task 权限、AgentProfile 工具和平台工具的交集，并产生可审计决策。 |
| `harness/tools/router.py` | ★ Tool 主路由 | 校验参数、执行 Policy、查写 Ledger、调用 Sandbox，并把结果和事件返回 AgentLoop。 |
| `harness/tools/ledger.py` | ★ 副作用账本 | 持久化 idempotency_key、started/succeeded/ambiguous 状态，防止恢复时重复副作用。 |
| `harness/tools/basic.py` | 基础工具实现 | 实现 `read_file`、`write_file`、`shell_exec`，所有操作只面向已绑定 Sandbox 工作区。 |

### 4.5 `harness/sandbox/`：单 Attempt 隔离环境

| 文件 | 定位 | 流程位置与职责 |
|---|---|---|
| `harness/sandbox/__init__.py` | Sandbox 层入口 | 暴露稳定 Runtime、Manager、请求结果和产物清单。 |
| `harness/sandbox/base.py` | ★ 隔离契约 | 定义 SandboxSpec、Handle、ExecRequest/Result、Info 和 Runtime 接口。 |
| `harness/sandbox/docker_runtime.py` | ★ 正式执行环境 | 通过参数数组调用 Docker；默认断网、非 root、只读根文件系统并限制 CPU/内存/PID/输出。 |
| `harness/sandbox/manager.py` | ★ 生命周期管理 | 为单 Attempt 创建、恢复、执行、取消、检查和销毁 Sandbox，并在销毁前生成 Artifact 哈希清单。 |

### 4.6 `harness/workspace/`：本地源码快照与回写

| 文件 | 定位 | 流程位置与职责 |
|---|---|---|
| `harness/workspace/__init__.py` | Workspace 层入口 | 暴露 WorkspaceSession、ChangeSet 和 WorkspaceManager。 |
| `harness/workspace/contracts.py` | ★ Workspace 契约 | 定义快照文件摘要、工作区会话与 ChangeSet，支持持久化和恢复。 |
| `harness/workspace/manager.py` | ★ 文件副作用边界 | 在 Sandbox 前生成本地快照；验收后计算 patch；检查源文件冲突并原子回写或回滚。 |

### 4.7 `harness/persistence/`：文件型持久化

| 文件 | 定位 | 流程位置与职责 |
|---|---|---|
| `harness/persistence/__init__.py` | 持久化入口 | 暴露各 Store、Lease 和 Checkpoint 契约。 |
| `harness/persistence/atomic.py` | ★ 原子写边界 | 提供同目录临时文件、fsync、原子替换和 OS 文件锁；防止并发覆盖与半写文件。 |
| `harness/persistence/task_store.py` | ★ 领域状态存储 | 保存不可变 Task、版本化 TaskExecution 和 Attempt，是状态机的事实存储。 |
| `harness/persistence/event_store.py` | ★ 审计事件存储 | 追加执行事件并维护序列，用于恢复、时间线和事故审计。 |
| `harness/persistence/checkpoint_store.py` | ★ 恢复存储 | 保存不可变 AgentLoop Checkpoint，支持服务重启后继续。 |
| `harness/persistence/locks.py` | ★ 单写者控制 | 管理 ExecutionLease 的 acquire/renew/assert/release，避免多个 Worker 同时推进一个 Execution。 |
| `harness/persistence/result_store.py` | 结果存储 | 原子保存 TaskResult 与 ValidationReport，供 GraphFlow 和 CLI 稳定读取。 |

### 4.8 `harness/observability/`：可观测性

| 文件 | 定位 | 流程位置与职责 |
|---|---|---|
| `harness/observability/__init__.py` | 可观测性入口 | 暴露 Timeline、Metrics 和 Redaction 能力。 |
| `harness/observability/model_events.py` | 模型事件 | 将 Model Gateway 调用转换为不包含密钥和原始敏感内容的 Harness 事件。 |
| `harness/observability/redaction.py` | ★ 信息安全 | 对日志、事件和评测载荷进行递归脱敏，位于数据离开执行内核前。 |
| `harness/observability/timeline.py` | 运维视图 | 按 execution_id 聚合状态、模型、工具、Policy、Sandbox 和验证事件。 |
| `harness/observability/metrics.py` | 指标视图 | 聚合成功率、轮次、Tool、Token、费用、延迟、拒绝和恢复指标。 |

## 5. `scheduler/`：GraphFlow 控制面

| 文件 | 定位 | Harness 关系与职责 |
|---|---|---|
| `scheduler/__init__.py` | 控制面包入口 | 标识 GraphFlow Runtime 包。 |
| `scheduler/main.py` | ★ CLI/Composition Root | 管理 Spec、Run、Plan、Graph、执行和恢复命令；正式 `execute` 在此注入 RuntimeFactory 生成的 Harness Handler。 |
| `scheduler/spec_flow.py` | 规格发布 | 校验 ChangeSpec/ProductSpec，生成不可变 SpecSnapshot 和修订审批结果。 |
| `scheduler/specs.py` | 规格契约 | 定义 SpecRef/SpecSnapshot、内容哈希和严格 Requirement/Acceptance 校验。 |
| `scheduler/spec_change.py` | 规格反馈 | 生成和校验 SpecChangeRequest，承接执行期发现的规格缺口。 |
| `scheduler/plans.py` | 执行计划 | 从已发布 SpecSnapshot 生成 ExecutionPlan，并按设计结果细化节点。 |
| `scheduler/design_spec.py` | 设计门禁 | 在 Requirement 与 ExecutionPlan 之间生成、校验 DesignSpec 和 Design Gate。 |
| `scheduler/drg.py` | ★ 图编译 | 把 ExecutionPlan 编译为有向执行图，校验节点、边、循环和图哈希。 |
| `scheduler/graph.py` | 图定义 | 保存静态 GraphFlow 边定义并输出人类可读图描述。 |
| `scheduler/runs.py` | ★ Run 状态 | 管理 GraphRun、Spec 绑定、Run 状态迁移和安全运行目录。 |
| `scheduler/executor.py` | ★ Graph 调度器 | 选择 ready nodes、处理重试/阻塞/人工门禁；正式 implementation/test/gate 节点交给 Harness Handler。 |
| `scheduler/agent_invoker.py` | 上下文准备 | 为旧 Agent 调用构建 ContextPack 和依赖 Artifact 引用；不应扩展为绕开 Harness 的新执行器。 |
| `scheduler/tool_contracts.py` | 控制面 Tool 声明 | 校验 GraphFlow 节点所需工具和 Artifact 输出；运行时真正权限由 Harness Policy/Router 执行。 |
| `scheduler/gates.py` | 遗留 Gate 执行 | 评估命令输出；正式 Harness 模式不得在宿主机直接调用其 Shell 路径。 |
| `scheduler/gate_auto.py` | Gate 适配 | 将 Spec acceptance 转换为 Harness Validator 可执行的结构化规则。 |
| `scheduler/artifacts.py` | Artifact 管理 | 管理 GraphFlow Run 级产物路径和写入；不负责 Sandbox 内部文件。 |
| `scheduler/evidence.py` | Evidence 汇总 | 生成 Run 级 EvidenceManifest，把节点产物、规格和验证证据汇总供审计。 |
| `scheduler/conformance.py` | 交付符合性 | 对已完成节点和产物生成 ConformanceReport，位于 Run 收尾门禁。 |
| `scheduler/intent_audit.py` | 意图一致性 | 确定性比较规格意图和产物结构，是 intended-vs-implemented 的结构检查部分。 |
| `scheduler/retry_policy.py` | 重试决策 | 结合 Harness 的 retryable/failure_kind 决定节点是否允许重试。 |
| `scheduler/retry.py` | 图重试管理 | 重置目标节点、失效下游节点并清理相关 Run 级产物。 |
| `scheduler/supersede.py` | Run 替代 | 将旧 Run 标记 superseded，并以新 SpecSnapshot 建立后继 Run。 |
| `scheduler/events.py` | 控制面事件 | 记录 Run/Node 事件；与 Harness 的 Execution 事件形成外层/内层两级时间线。 |
| `scheduler/path_security.py` | ★ 共用安全边界 | 校验路径片段并确保路径位于授权根目录；GraphFlow 与 Harness 文件入口共同使用。 |
| `scheduler/audit.py` | 仓库审计 | 扫描 TaskCard、Artifact 和状态违规，服务于遗留 GraphFlow 审计。 |
| `scheduler/schema.py` | 遗留契约校验 | 校验 TaskCard、DONE 转换、UI 设计与 Agent 输出；新 Harness 契约以 `harness/contracts.py` 为准。 |
| `scheduler/state.py` | 遗留/共享状态 | 定义 TaskCard、ValidationIssue/Result 等；其中 GraphFlow Run 应优先使用 `runs.py`。 |
| `scheduler/transition.py` | 遗留状态检查 | 校验 TaskCard 状态迁移和 Artifact 前置条件，不替代 Harness 状态机。 |
| `scheduler/task_card.py` | 遗留输入解析 | 读取 Markdown TaskCard/frontmatter/done items，主要用于兼容和迁移。 |
| `scheduler/legacy.py` | 迁移适配 | 将旧 TaskCard 转换为 ChangeSpec，避免旧格式继续渗入 Harness。 |
| `scheduler/ui_design.py` | UI 规格支撑 | 校验产品形态字段，为 UI Design Agent 和规格门禁提供确定性约束。 |
| `scheduler/__init__.pyc` | 生成物/不应入库 | Python 字节码，不属于架构源码；不应作为修改目标，建议后续从版本控制移除并由 `.gitignore` 排除。 |

## 6. `agents/`：版本化 Agent 定义输入

这些 Markdown 文件是 GraphFlow 的角色定义或 AgentProfile 迁移来源。Prompt 负责“引导”，Tool Policy、Sandbox、预算和 Validator 才是不可绕过的硬约束。

| 文件 | 定位 | Harness 关系 |
|---|---|---|
| `agents/README.md` | Agent 目录说明 | 解释角色文件组织和使用方式。 |
| `agents/_schema.md` | 输出约束 | 定义 Agent Markdown/结构化输出应满足的公共 Schema。 |
| `agents/orchestrator.md` | 编排角色 | 负责跨 Task 拆分和分配，属于 GraphFlow，不应进入单 Task AgentLoop。 |
| `agents/spec-agent.md` | 规格角色 | 形成 Requirement/Acceptance 和规格修订输入。 |
| `agents/architect-agent.md` | 架构角色 | 生成架构决策与设计边界，供 DesignSpec/Plan 使用。 |
| `agents/backend-dev.md` | 实现角色 | 可迁移为版本化 AgentProfile，执行后端单 Task。 |
| `agents/frontend-dev.md` | 实现角色 | 可迁移为版本化 AgentProfile，执行前端单 Task。 |
| `agents/client-dev.md` | 实现角色 | 可迁移为版本化 AgentProfile，执行客户端单 Task。 |
| `agents/ui-design-agent.md` | 设计角色 | 生成 UI 设计产物，受 UI Design Contract 和 Gate 校验。 |
| `agents/test-agent.md` | 测试角色 | 生成/执行测试任务，正式命令必须通过 Harness Sandbox。 |
| `agents/review-agent.md` | 评审角色 | 对实现或证据提出评审结果，不直接授予工具权限。 |
| `agents/gate-auto.md` | 门禁角色 | 描述自动 Gate 行为，确定性判定仍由代码 Validator/Gate 实现。 |
| `agents/devops-agent.md` | 运维角色 | 描述环境和发布任务；MVP 中不开放真实外部部署副作用。 |

## 7. `specs/`：规格事实与版本输入

| 文件 | 定位 | Harness 关系 |
|---|---|---|
| `specs/dahua-client/product-spec.json` | 产品规格 | 提供跨版本稳定产品背景，发布 ChangeSpec 时合并。 |
| `specs/dahua-client/drafts/dahua-client-mvp-change-spec.json` | 规格草稿 | 尚未成为运行基线的 ChangeSpec 输入。 |
| `specs/dahua-client/published/dahua-client-mvp/v1/spec-snapshot.json` | 发布快照 | GraphRun 可绑定的不可变 v1 规格和验收来源。 |
| `specs/dahua-client/published/dahua-client-mvp/v2/spec-snapshot.json` | 发布快照 | GraphRun 可绑定的不可变 v2 规格和验收来源。 |

## 8. `docker/`、`scripts/` 与 `tests/`：隔离和验证面

### 8.1 Docker 与工程门禁

| 文件 | 定位 | Harness 关系 |
|---|---|---|
| `docker/harness-python.Dockerfile` | ★ Sandbox 镜像 | 定义非 root Python Sandbox 基础环境；生产应以镜像摘要启动。 |
| `scripts/__init__.py` | 脚本包入口 | 标识 CI/发布检查脚本集合。 |
| `scripts/check_harness_comments.py` | ★ 注释门禁 | 扫描 Harness 公共符号，强制中文职责与流程位置说明。 |
| `scripts/check_harness_release_gate.py` | ★ 发布门禁 | 检查 Golden 覆盖、危险 Shell、Fake/Mock 正式引用等发布条件。 |

### 8.2 自动化测试

| 文件 | 定位 | 覆盖范围 |
|---|---|---|
| `tests/test_scheduler_runtime.py` | GraphFlow 回归 | 覆盖 Spec、Plan、DRG、Run、Retry、Gate、Evidence 和 CLI 主流程。 |
| `tests/test_harness_stage1.py` | Harness 阶段 1 | Contracts、Store、状态机、租约、Checkpoint 和事件。 |
| `tests/test_harness_stage2.py` | Harness 阶段 2 | AgentProfile、Model Gateway、Context 和 Schema 校验。 |
| `tests/test_harness_stage3.py` | Harness 阶段 3 | Tool Registry/Policy/Router/Ledger、Sandbox 和安全边界。 |
| `tests/test_harness_stage4.py` | Harness 阶段 4 | AgentLoop、预算、取消、暂停、Checkpoint、恢复和不确定副作用。 |
| `tests/test_harness_stage5.py` | Harness 阶段 5 | Validator、TaskResult、GraphFlow Handler 和节点结果映射。 |
| `tests/test_harness_stage6.py` | Harness 阶段 6 | Timeline、Metrics、Golden Evaluation、版本比较和发布门禁。 |
| `tests/test_harness_stage7.py` | Harness 阶段 7 | 本地快照、ChangeSet 回写、冲突拒绝、异常收口、OS 锁和 RuntimeFactory。 |
| `tests/test_harness_docker_smoke.py` | 真实环境冒烟 | 在 Docker 可用时验证真实容器创建、执行、限制和销毁；不可用时显式跳过。 |
| `tests/test_harness_comment_policy.py` | 文档质量门禁 | 验证中文职责/位置注释策略本身持续生效。 |

### 8.3 Golden Cases

| 文件 | 定位 | Harness 关系 |
|---|---|---|
| `tests/golden/README.md` | 评测说明 | 解释 Golden Case 的运行和维护方式。 |
| `tests/golden/schema.json` | Case Schema | 约束评测输入、环境、断言和版本信息。 |
| `tests/golden/cases.json` | 评测样本 | 保存正常、越权、取消、预算、恢复、验证失败和基础设施失败场景。 |
| `tests/golden/coverage.json` | 覆盖映射 | 把 P0 Golden 场景映射到真实自动化测试，供发布门禁检查。 |

## 9. `documentation/`：当前 Harness 核心文档

| 文件 | 定位 | 用途 |
|---|---|---|
| `documentation/agent-loop-mvp-review-baseline.md` | ★ 评审基线 | 定义 Agent-Loop/Harness MVP 必备模块、评级和硬门槛。 |
| `documentation/spec-graphflow-current-gap-review.md` | 缺口审计 | 记录原 SPEC-GraphFlow 相对目标 Harness 的 P0/P1 缺口。 |
| `documentation/architecture-current-vs-agent-harness.md` | 架构对比 | 对比当前与标准 Agent-Harness 架构及演进方向。 |
| `documentation/agent-harness-mvp-implementation-plan.md` | ★ 实施计划 | 按阶段说明修改位置、流程环节、验收标准和已完成进度。 |
| `documentation/project-path-tree-harness-positioning.md` | ★ 路径索引 | 本文档；作为代码评审、交接和后续 Agent 修改前的导航入口。 |

## 10. `doc/`：原始设计、分析与历史评审

| 文件 | 定位 | 与当前 Harness 的关系 |
|---|---|---|
| `doc/architecture.md` | 原系统架构 | 描述 SPEC-GraphFlow 既有架构，作为迁移背景，不代表全部现状。 |
| `doc/design.md` | 原设计说明 | 描述早期多 Agent 和 GraphFlow 设计决策。 |
| `doc/graphflow-runtime.md` | GraphFlow Runtime | 解释控制面运行结构和节点推进方式。 |
| `doc/graphflow-system-operation.md` | 运维说明 | 记录 GraphFlow 系统操作方法。 |
| `doc/multi-agent-flow.md` | 多 Agent 规划 | 描述远期多 Agent 流程；当前单 Agent MVP 不实现其中并行协作。 |
| `doc/SpecGraphflow-Spec层规约模式分析.md` | Spec 分析 | 分析 Spec 层规约模式及其约束。 |
| `doc/Spec驱动GraphFlow全链路交付开发系统架构说明书.md` | 全链路说明 | 描述 Spec 驱动交付系统的完整设想。 |
| `doc/system-audit.md` | 历史审计 | 记录原系统完整性、Agent 和流程缺口。 |
| `doc/Task-centric-Harness架构符合性评审.md` | Harness 评审 | 从 Task-centric Harness 视角评估原系统符合性。 |
| `doc/以Task为基准的Harness架构设计.md` | Harness 目标设计 | 描述以 Task 为边界的目标 Harness 架构。 |

## 11. `00-UpdatePlan/` 与 `shared/`：演进输入和共享说明

| 文件 | 定位 | 作用 |
|---|---|---|
| `00-UpdatePlan/01-requirement-acceptance改动.md` | 演进输入 | 记录 Requirement/Acceptance 的改动设想。 |
| `00-UpdatePlan/02-Currentdelta-Spec变更.md` | 演进输入 | 记录 Current→Delta Spec 变更思路；文件名包含特殊字符，自动化处理时必须使用字面路径。 |
| `00-UpdatePlan/03-DesignSpec优化.md` | 演进输入 | 记录 DesignSpec 优化方向。 |
| `00-UpdatePlan/04-ExcutionPlan优化.md` | 演进输入 | 记录 ExecutionPlan 优化方向，文件名保留历史拼写。 |
| `shared/api-contract.md` | 共享契约 | 记录 Agent 或模块协作时的 API 数据约定。 |
| `shared/blackboard.md` | 共享黑板 | 记录原多 Agent 协作信息；当前 Harness 不以它作为运行时事实存储。 |

## 12. 单 Task 执行链与修改落点

| 执行阶段 | 首要文件 | 常见修改原因 |
|---|---|---|
| GraphFlow 选择节点 | `scheduler/executor.py` | ready-node、阻塞、重试或人工门禁策略变化。 |
| Node 转 Task | `harness/integration/graphflow_handler.py` | Task 输入、预算、权限、Acceptance 或结果映射变化。 |
| 运行时装配 | `harness/runtime_factory.py` | Provider、Sandbox、Policy 或 Profile 绑定变化。 |
| Task/Attempt 建立 | `harness/controller.py`、`contracts.py` | 生命周期或持久化契约变化。 |
| 源码准备 | `harness/workspace/manager.py` | 快照范围、排除规则、ChangeSet 或回写策略变化。 |
| Sandbox 启动 | `harness/sandbox/manager.py`、`docker_runtime.py` | 隔离、资源配额、镜像或恢复方式变化。 |
| Agent 推理循环 | `harness/agent_loop.py`、`context_manager.py` | 模型上下文、轮次、Checkpoint 或完成协议变化。 |
| 模型调用 | `harness/models/gateway.py`、`provider_adapter.py` | Provider、重试、超时、Schema 或费用变化。 |
| 工具调用 | `harness/tools/router.py`、`policy.py`、`ledger.py` | 新工具、权限、幂等性或副作用控制变化。 |
| 确定性验收 | `harness/validation.py` | Acceptance 类型或成功标准变化。 |
| 结果提交/回写 | `harness/task_runner.py`、`workspace/manager.py` | TaskResult、Evidence、冲突处理或回写行为变化。 |
| 恢复与治理 | `harness/recovery.py`、`worker.py` | 崩溃恢复、租约、孤儿 Sandbox 或人工对账变化。 |
| 观测与评测 | `harness/observability/`、`evaluation.py` | 时间线、指标、脱敏或版本回归评测变化。 |

## 13. 文档维护规则

1. 新增源文件时，必须在本文档对应目录表中增加一行，写明职责和流程位置；
2. 改变 GraphFlow/Harness 边界时，必须同时更新第 1 节架构图和第 12 节执行链；
3. 新增正式 Tool 时，至少同步更新 Registry、Policy、权限契约、Golden Case 和本文档；
4. 新增状态时，必须同步更新 Contracts、状态机、Controller、恢复判定、Timeline 和测试；
5. 新增环境变量或密钥时，应补充独立变量/密钥文档，禁止只写在代码或本地环境中；
6. `.loop/`、`__pycache__/`、`.pyc`、测试缓存和临时文件属于运行生成物，不作为架构文件维护；
7. 历史 `doc/` 描述与实现冲突时，以当前 `documentation/`、稳定契约和自动化测试为评审依据，并记录差异。

