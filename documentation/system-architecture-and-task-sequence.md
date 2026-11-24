# 系统架构与任务执行时序

本文记录当前项目的系统架构以及单个 Task 从 Spec 进入 GraphFlow、由 Harness 执行并返回结果的主要过程。

设计原则可以概括为：

- Spec 定义目标、约束和验收标准；
- GraphFlow 负责编排任务及处理流程分支；
- Harness 负责受控运行单个 Task；
- Artifact、Validation 和 Evidence 负责证明结果。

两张图的独立 Mermaid 源文件位于：

- [`diagrams/system-architecture.mmd`](diagrams/system-architecture.mmd)
- [`diagrams/task-execution-sequence.mmd`](diagrams/task-execution-sequence.mmd)

## 系统架构图

```mermaid
flowchart TB
    U[用户 / 产品负责人]

    subgraph SPEC["Spec 规约层"]
        CS[ChangeSpec 草稿]
        PS[ProductSpec]
        SNAP[Published SpecSnapshot<br/>不可变版本 + 内容哈希]
        CS --> SNAP
        PS --> SNAP
    end

    subgraph GRAPH["GraphFlow 控制平面"]
        RUN[GraphRun]
        PLAN[ExecutionPlan]
        DRG[ExecutionGraph / DRG]
        EXEC[DRGExecutor]
        FLOW[规划 → 架构 → 设计 → 实现<br/>测试 → 验收 → 评审 → 交付]
        RUN --> PLAN --> DRG --> EXEC --> FLOW
    end

    subgraph ADAPTER["GraphFlow / Harness 边界"]
        HANDLER[HarnessNodeHandler]
        TASK[Task Contract]
        RESULT[TaskResult]
        HANDLER --> TASK
        RESULT --> HANDLER
    end

    subgraph HARNESS["Task-centric Harness 执行平面"]
        CTRL[HarnessController<br/>Execution / Attempt / 状态机]
        PROFILE[AgentProfile<br/>Prompt / Model / Tools / Version]
        CTX[ContextManager]
        LOOP[AgentLoop]
        MODEL[ModelGateway]
        ROUTER[ToolRouter]
        POLICY[PolicyEngine]
        LEDGER[幂等 Tool Ledger]
        VALIDATOR[DeterministicValidator]

        CTRL --> CTX
        PROFILE --> CTX
        CTX --> LOOP
        LOOP <--> MODEL
        LOOP --> ROUTER
        ROUTER --> POLICY
        ROUTER --> LEDGER
        LOOP --> VALIDATOR
    end

    subgraph ISOLATION["隔离执行与源码回写"]
        WORKSPACE[Workspace Snapshot]
        DOCKER[Docker Sandbox]
        TOOLS[read_file / write_file / shell_exec]
        CHANGESET[ChangeSet]
        WRITEBACK[验收后原子回写]

        WORKSPACE --> DOCKER --> TOOLS
        DOCKER --> CHANGESET --> WRITEBACK
    end

    subgraph GOVERNANCE["治理与可观测性"]
        BUDGET[轮次 / Token / Tool / 时间 / 成本预算]
        CHECKPOINT[Checkpoint / 暂停 / 恢复]
        EVENT[Event / Timeline / Metrics]
        EVIDENCE[Artifact / Validation / Evidence]
        GOLDEN[Golden Cases / 版本对比 / 发布门禁]
    end

    U --> CS
    SNAP --> RUN
    EXEC --> HANDLER
    TASK --> CTRL
    ROUTER --> DOCKER
    VALIDATOR --> CHANGESET
    WRITEBACK --> RESULT
    CTRL --> CHECKPOINT
    CTRL --> EVENT
    RESULT --> EVIDENCE
    EVIDENCE --> EXEC
    GOLDEN --> HARNESS
```

## Task 执行时序图

```mermaid
sequenceDiagram
    autonumber

    actor User as 用户/产品负责人
    participant Spec as SpecFlow
    participant Graph as GraphFlow
    participant Adapter as HarnessNodeHandler
    participant Runner as HarnessTaskRunner
    participant Controller as HarnessController
    participant Agent as AgentLoop
    participant Model as ModelGateway
    participant Router as ToolRouter/Policy
    participant Sandbox as Docker Sandbox
    participant Validator as Validator
    participant Workspace as WorkspaceManager

    User->>Spec: 提交并批准 ChangeSpec
    Spec->>Spec: 校验 Requirement / Acceptance
    Spec->>Graph: 发布不可变 SpecSnapshot

    Graph->>Graph: 创建 GraphRun
    Graph->>Graph: 生成 ExecutionPlan
    Graph->>Graph: 编译 ExecutionGraph
    Graph->>Adapter: 执行 implementation 节点

    Adapter->>Adapter: Graph 节点转换为 Task Contract
    Adapter->>Runner: run(Task, AgentProfile)

    Runner->>Workspace: 创建源码快照
    Runner->>Controller: 创建 Execution + Attempt + Lease
    Controller->>Sandbox: 创建隔离工作区和容器

    loop Agent 多轮执行
        Runner->>Agent: objective + acceptance + budget
        Agent->>Model: 受预算约束的模型请求
        Model-->>Agent: ToolCall 或 Complete

        alt 模型请求工具
            Agent->>Router: read_file/write_file/shell_exec
            Router->>Router: Schema + 权限交集 + 幂等检查
            Router->>Sandbox: 执行已授权操作
            Sandbox-->>Router: ToolResult
            Router-->>Agent: Observation
        else 模型声明完成
            Agent-->>Runner: Completion
        end
    end

    Runner->>Validator: 确定性验收
    Validator->>Router: 执行 acceptance checks
    Router->>Sandbox: 测试命令 argv
    Sandbox-->>Validator: exit code + stdout hash

    alt 验收通过
        Runner->>Workspace: 生成 ChangeSet
        Workspace->>Workspace: 检查源文件并发冲突
        Workspace->>Workspace: 原子回写本地工程
        Runner->>Controller: SUCCEEDED
        Runner-->>Adapter: TaskResult + Artifact + Evidence
        Adapter-->>Graph: 节点成功，推进下游
    else 验收失败
        Runner->>Controller: FAILED
        Runner-->>Adapter: failure_kind + retryable
        Adapter-->>Graph: 重试 / SpecChange / 人工处理
    end
```

## 图示边界

架构图描述的是当前代码已经形成的主要边界，不代表所有 GraphFlow 节点都已经接入正式 Harness。当前正式 Harness 主要接管 implementation、test、gate 和 plan_gate；review、deploy 仍需要真实生产 Handler。

时序图描述的是正式 Harness 路径。`smoke-flow` 使用确定性 mock 路径，其结果只能作为流程自检证据，不能替代真实模型执行、评审或部署证据。
