# 当前 SPEC-GraphFlow 与目标 Agent-Harness 架构对比

## 1. 阅读方式

本文包含三张架构图：

1. 当前项目的真实实现架构；
2. 标准 Agent-Harness 目标架构；
3. 从当前架构向 Harness MVP 演进的迁移关系。

图中的标记含义：

- `[保留]`：现有能力可以直接复用；
- `[重构]`：现有能力保留职责，但需要调整边界或数据模型；
- `[新增]`：当前不存在，是 Harness MVP 的核心建设项；
- `[风险]`：当前实现中必须优先关闭的危险路径；
- `[未来]`：不属于 MVP，可以在核心闭环稳定后演进。

---

## 2. 当前项目：SPEC-GraphFlow 真实架构

```mermaid
flowchart TB
    subgraph INPUT[规格输入层]
        CS[ChangeSpec Draft]
        PS[ProductSpec]
        APPROVAL[Revision Approval]
        SNAP[Published SpecSnapshot<br/>不可变版本 + content hash]
        CS --> SNAP
        PS --> SNAP
        APPROVAL --> SNAP
    end

    subgraph GRAPH[GraphFlow 控制平面]
        CREATE[GraphRun 创建与 Spec 绑定]
        PLAN[ExecutionPlan 生成]
        DRG[DRG 编译<br/>ExecutionGraph]
        CONTRACT[ToolContract 静态校验]
        EXEC[DRGExecutor<br/>确定性节点调度]
        CREATE --> PLAN --> DRG --> CONTRACT --> EXEC
    end

    SNAP --> CREATE

    subgraph NODES[执行图节点]
        BIND[Spec Binding]
        PLAN_GATE[Plan Gate]
        ARCH[Architecture Impact]
        DESIGN[Design Spec / Design Gate]
        IMPL[Implementation Node]
        TEST[Test Node]
        FINAL_GATE[Final Gate]
        CONF[Conformance]
        REVIEW[Delivery Review]
        DEPLOY[Deploy]
        DONE[Done]
        BIND --> PLAN_GATE --> ARCH --> DESIGN --> IMPL --> TEST --> FINAL_GATE --> CONF --> REVIEW --> DEPLOY --> DONE
    end

    EXEC --> NODES

    subgraph HANDLER[节点执行适配层]
        CTX[AgentContextPack Builder<br/>仅生成上下文引用文件]
        INJECTED[可选 Python Node/Agent Handler]
        FALLBACK[确定性 Fallback Handler<br/>可生成 success 产物]
        SHELL[Gate Shell Runner<br/>subprocess + shell=True]
        CTX --> INJECTED
        CTX --> FALLBACK
        TEST --> SHELL
        FINAL_GATE --> SHELL
    end

    EXEC --> CTX
    INJECTED --> EXEC
    FALLBACK --> EXEC
    SHELL --> EXEC

    subgraph GOVERNANCE[治理与恢复]
        RETRY[RetryPolicy / RetryManager]
        HUMAN[AWAITING_HUMAN<br/>人工评审暂停/恢复]
        SPEC_CHANGE[AWAITING_SPEC_CHANGE<br/>SCR / Revision / Rebind]
        SUPERSEDE[Run Supersede]
    end

    EXEC --> RETRY
    EXEC --> HUMAN
    EXEC --> SPEC_CHANGE
    SPEC_CHANGE --> SUPERSEDE
    RETRY --> EXEC
    HUMAN --> EXEC
    SPEC_CHANGE --> PLAN

    subgraph STORE[文件型运行存储 .loop/runs/run_id]
        STATE[run-state.json]
        SNAPSHOTS[state-snapshots]
        EVENTS[events.jsonl]
        CONTEXT[context-packs]
        ARTIFACTS[artifacts]
        EVIDENCE[evidence-manifest.json]
        CONFORMANCE[conformance-report.json]
    end

    EXEC --> STATE
    EXEC --> SNAPSHOTS
    EXEC --> EVENTS
    CTX --> CONTEXT
    EXEC --> ARTIFACTS
    DONE --> EVIDENCE
    CONF --> CONFORMANCE

    subgraph LEGACY[遗留兼容路径]
        TASKCARD[Legacy TaskCard]
        OLD_STATE[TaskCard 状态机]
        OLD_ARTIFACTS[Legacy ArtifactManager]
        TASKCARD --> OLD_STATE --> OLD_ARTIFACTS
        TASKCARD -.转换.-> CS
    end

    subgraph MISSING[当前 Harness 内核缺口]
        NO_LOOP[[缺少真实 Agent Loop]]
        NO_MODEL[[缺少 Model Gateway]]
        NO_ROUTER[[缺少 Tool Router / PolicyDecision]]
        NO_SANDBOX[[缺少 Sandbox]]
        NO_ATTEMPT[[缺少 TaskExecution / Attempt]]
        NO_EVAL[[缺少 Golden Case Evaluation Harness]]
    end

    CTX -.没有实际调用.-> NO_MODEL
    FALLBACK -.替代了真实执行.-> NO_LOOP
    SHELL -.绕过.-> NO_ROUTER
    SHELL -.宿主直接执行.-> NO_SANDBOX
    STATE -.模型粒度不足.-> NO_ATTEMPT
    EVENTS -.仅节点级观测.-> NO_EVAL
```

### 2.1 当前架构的本质

当前系统的强项在 GraphFlow 控制平面：它能够把 Spec 变成计划和执行图，并管理节点状态、重试、暂停、Artifact、Evidence 和 Conformance。

但节点内部仍不是 Agent-Harness：`AgentContextPack` 只准备输入引用，实际执行依赖可选 Python Handler；没有 Handler 时，系统会使用确定性 fallback 生成成功结果。验收 Shell 又直接运行在宿主工作区中。

因此当前的 `DONE` 主要表示“执行图已走完”，不必然表示“真实 Agent 已完成并通过工程验收”。

---

## 3. 标准 Agent-Harness 目标架构

```mermaid
flowchart TB
    subgraph ENTRY[入口与外层编排]
        USER[用户 / API / CLI]
        GRAPHFLOW[GraphFlow Orchestrator<br/>Spec / Plan / DRG]
        TASK[Task Contract<br/>目标、输入、权限、预算、验收]
        USER --> GRAPHFLOW --> TASK
    end

    subgraph CONTROL[Harness 控制平面]
        CONTROLLER[Task Controller]
        EXECUTION[TaskExecution]
        ATTEMPT[Attempt]
        STATESTORE[Durable State Store<br/>事务、版本、租约]
        BUDGET[Budget & Usage Meter<br/>轮次、Token、费用、Tool、时间]
        CANCEL[Cancel / Timeout Controller]
        RECOVERY[Recovery Reconciler<br/>孤儿任务与资源回收]

        TASK --> CONTROLLER --> EXECUTION --> ATTEMPT
        EXECUTION <--> STATESTORE
        ATTEMPT <--> STATESTORE
        ATTEMPT --> BUDGET
        CONTROLLER --> CANCEL
        RECOVERY <--> STATESTORE
    end

    subgraph AGENT[Agent Runtime]
        PROFILE[Versioned AgentProfile<br/>Prompt、Model、Tool 偏好]
        CONTEXT[Context Manager<br/>历史、摘要、来源、Token 预算]
        LOOP[Agent Loop<br/>Reason → Act → Observe → Evaluate]
        MODEL[Model Gateway<br/>路由、Schema、重试、计量]
        LLM[LLM Provider]

        PROFILE --> CONTEXT
        ATTEMPT --> CONTEXT --> LOOP
        LOOP --> MODEL --> LLM
        LLM --> MODEL --> LOOP
        BUDGET --> LOOP
        CANCEL --> LOOP
    end

    subgraph TOOLS[工具控制平面]
        CALL[ToolCall<br/>call_id + schema + idempotency_key]
        ROUTER[Tool Router / Registry]
        POLICY[Policy Engine<br/>Task ∩ Agent ∩ User ∩ Platform]
        APPROVAL[Approval Service<br/>WAITING_APPROVAL]
        LEDGER[Side-effect Ledger<br/>幂等与恢复]

        LOOP --> CALL --> ROUTER --> POLICY
        POLICY -->|需要审批| APPROVAL
        APPROVAL -->|批准| POLICY
        POLICY -->|允许| LEDGER
        POLICY -->|拒绝| RESULT
    end

    subgraph EXECUTORS[受控执行平面]
        SANDBOX[Sandbox Manager]
        WORKER[Sandbox Worker<br/>文件、Shell、构建、测试]
        APIEXEC[Controlled API Executor<br/>Git、DB、部署、外部服务]
        SECRETS[Secret Manager<br/>临时最小权限注入]

        LEDGER -->|代码与 Shell 工具| SANDBOX --> WORKER
        LEDGER -->|业务 API 工具| APIEXEC
        SECRETS --> WORKER
        SECRETS --> APIEXEC
        CANCEL --> SANDBOX
        RECOVERY --> SANDBOX
    end

    subgraph RESULTING[结果与验证]
        RESULT[ToolResult / Observation<br/>completed、failed、timeout、denied]
        VALIDATE[Deterministic Validator<br/>测试、Schema、规则]
        TASKRESULT[TaskResult<br/>succeeded / failed / cancelled / timed_out]
        ARTIFACT[Artifact & Evidence Store]

        WORKER --> RESULT
        APIEXEC --> RESULT
        RESULT --> LOOP
        LOOP -->|提出完成| VALIDATE
        VALIDATE -->|可修复且预算允许| LOOP
        VALIDATE --> TASKRESULT
        TASKRESULT --> ARTIFACT
        TASKRESULT --> GRAPHFLOW
    end

    subgraph OBSERVE[审计、观测与评测]
        EVENT[Event Store<br/>event_id / sequence / causation]
        TRACE[Trace & Metrics<br/>模型、Tool、Policy、成本、延迟]
        REPLAY[Run Replay / Timeline]
        EVAL[Evaluation Harness<br/>Golden Cases / Regression / Compare]

        CONTROLLER --> EVENT
        LOOP --> EVENT
        POLICY --> EVENT
        RESULT --> EVENT
        EVENT --> TRACE
        EVENT --> REPLAY
        TASKRESULT --> EVAL
        ARTIFACT --> EVAL
    end

    subgraph FUTURE[稳定后再演进]
        QUEUE[Distributed Queue]
        MULTI[Multi-Agent / Subtasks]
        REMOTE[Remote Sandbox / Kubernetes / MicroVM]
    end

    CONTROLLER -.未来.-> QUEUE
    LOOP -.未来.-> MULTI
    SANDBOX -.未来.-> REMOTE
```

### 3.1 标准 Harness 的职责边界

```text
GraphFlow 决定：做哪些 Task、依赖关系是什么、何时推进或回退。
Harness 决定：一个 Task 如何被 Agent 安全、可靠、可恢复地完成。
Agent 决定：下一步尝试什么动作。
Policy 决定：这个动作是否允许。
Executor 决定：如何在受控环境执行动作。
Validator 决定：结果是否客观满足验收条件。
```

---

## 4. 当前能力到目标架构的迁移图

```mermaid
flowchart LR
    subgraph CURRENT[当前 SPEC-GraphFlow]
        C_SPEC[SpecSnapshot]
        C_DRG[ExecutionPlan / DRG]
        C_RUN[GraphRun]
        C_CONTRACT[ToolContract]
        C_CTX[ContextPack]
        C_RETRY[RetryPolicy]
        C_EVENT[EventLogger]
        C_ART[Artifact / Evidence]
        C_GATE[Gate / Conformance]
        C_HUMAN[AWAITING_HUMAN]
    end

    subgraph TARGET[目标 Agent-Harness]
        T_TASK[Task Contract]
        T_ORCH[GraphFlow Orchestrator]
        T_EXEC[TaskExecution / Attempt]
        T_PROFILE[AgentProfile + Policy Input]
        T_CONTEXT[Context Manager]
        T_RETRY[Orchestration Retry<br/>+ Attempt Retry]
        T_EVENT[Task / Model / Tool / Policy Events]
        T_ART[Artifact / Evidence Store]
        T_VALID[Deterministic Validator]
        T_APPROVAL[Approval Service]
    end

    C_SPEC -->|保留并映射| T_TASK
    C_DRG -->|保留在外层| T_ORCH
    C_RUN -->|拆分| T_EXEC
    C_CONTRACT -->|作为输入，不作为最终权限| T_PROFILE
    C_CTX -->|扩展| T_CONTEXT
    C_RETRY -->|拆分职责| T_RETRY
    C_EVENT -->|扩展事件粒度| T_EVENT
    C_ART -->|保留并增强版本/哈希| T_ART
    C_GATE -->|保留确定性检查| T_VALID
    C_HUMAN -->|扩展身份、范围、过期| T_APPROVAL

    subgraph NEW[必须新增的 Harness 内核]
        N_LOOP[Agent Loop]
        N_MODEL[Model Gateway]
        N_ROUTER[Tool Router]
        N_POLICY[Policy Engine]
        N_SANDBOX[Sandbox Manager]
        N_BUDGET[Budget / Usage]
        N_LEDGER[Side-effect Ledger]
        N_RESULT[TaskResult]
        N_EVAL[Evaluation Harness]
        N_RECOVERY[Cancel / Recovery Reconciler]
    end

    T_EXEC --> N_LOOP
    T_CONTEXT --> N_LOOP
    N_LOOP --> N_MODEL
    N_LOOP --> N_ROUTER --> N_POLICY --> N_SANDBOX
    T_PROFILE --> N_POLICY
    N_BUDGET --> N_LOOP
    N_POLICY --> N_LEDGER
    N_SANDBOX --> N_RESULT
    T_VALID --> N_RESULT
    N_RESULT --> T_ORCH
    N_RESULT --> N_EVAL
    N_RECOVERY --> T_EXEC
    N_RECOVERY --> N_SANDBOX
```

---

## 5. 修订方向与优先级

### P0：先关闭当前危险路径

```mermaid
flowchart LR
    A[禁止正式模式 fallback success]
    B[禁止 Spec 命令宿主 shell=True]
    C[增加路径边界与 run_id 校验]
    D[强制总超时、取消和清理]
    A --> B --> C --> D
```

目标：让系统在缺少真实能力时 fail-closed，而不是产生虚假的 DONE。

### P1：建立第一个 Harness 纵向切片

```mermaid
flowchart LR
    NODE[一个 implementation Node]
    TASK[TaskExecution]
    LOOP[单 Agent Loop]
    MODEL[Model Gateway]
    TOOLS[read_file / write_file / shell_test]
    POLICY[Tool Router + Policy]
    SANDBOX[单 Sandbox]
    VERIFY[确定性测试验收]
    RESULT[TaskResult + Evidence]
    NODE --> TASK --> LOOP --> MODEL --> LOOP
    LOOP --> TOOLS --> POLICY --> SANDBOX --> LOOP
    LOOP --> VERIFY --> RESULT --> NODE
```

目标：先证明一个真实工程 Task 能够安全完成、失败、取消、恢复和审计。

### P2：把 Harness 接回所有 GraphFlow 节点

- implementation、test、review、deploy 节点统一提交 Task；
- GraphFlow 不再理解 LLM 轮次和 ToolCall；
- Harness 不负责跨 Task 的业务编排；
- RetryPolicy 拆分为 GraphFlow 节点重试和 Harness Attempt 重试；
- AWAITING_HUMAN 扩展为绑定具体动作的 ApprovalRequest。

### P3：形成可持续演进基础设施

- Golden Case Evaluation Harness；
- 模型、Prompt、Agent、Tool、Policy 和 Runtime 版本闭包；
- CI 回归和发布门禁；
- 多 Worker、队列、租约和 Reconciler；
- 多 Agent、远程 Sandbox、Kubernetes 或 MicroVM。

---

## 6. 最关键的设计决策

后续修订时应坚持以下边界：

1. GraphFlow 保留为外层确定性编排，不承载 Agent 内部循环；
2. Harness 以 TaskExecution 为核心，而不是继续扩张 GraphRun；
3. AgentProfile 只声明能力偏好，不授予权限；
4. 所有 ToolCall 经过同一个 Tool Router 和 Policy Engine；
5. Shell、构建和测试必须进入 Sandbox；
6. 模型自称完成不能直接产生 succeeded，必须经过 Validator；
7. 重试必须区分新的 Attempt，并识别已经发生的副作用；
8. 每次运行必须固定完整版本闭包并可按事件回放；
9. MVP 先完成单 Task、单 Agent、单 Sandbox，再考虑多 Agent 和分布式。

