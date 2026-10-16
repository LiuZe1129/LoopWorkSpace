# 共享黑板（Blackboard）

> 所有 Agent 共享的结构化状态空间。记录任务分配、执行结果、决策记录、Trace 日志。
> v2.0：消息改为结构化 Schema，Agent 间交接传 artifact 引用不传内容。
> v3.0：Blackboard 是人类可读摘要视图；GraphFlow Runtime 的机器真源为 `.loop/events/{task_id}.jsonl`。

---

## 1. 消息 Schema

每条消息必须符合以下结构：

```json
{
  "msg_id": "msg-001",
  "timestamp": "2026-08-10T10:00:00Z",
  "from": "scheduler",
  "to": "backend-dev",
  "type": "task_assign",
  "task_id": "task-001",
  "round": 1,
  "artifacts": [
    {
      "type": "artifact",
      "path": ".loop/artifacts/{project}/task-001/round-1/architecture.json",
      "description": "架构决策"
    }
  ],
  "verdict": null,
  "summary": "分配后端开发任务"
}
```

### 消息字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `msg_id` | 是 | 唯一消息 ID（递增） |
| `timestamp` | 是 | ISO 8601 时间戳 |
| `from` | 是 | 发送方（scheduler / agent-name） |
| `to` | 是 | 接收方（scheduler / agent-name） |
| `type` | 是 | 消息类型（见下表） |
| `task_id` | 是 | 关联任务 ID |
| `round` | 是 | 第几轮循环 |
| `artifacts` | 是 | 产物引用列表（路径，非内容） |
| `verdict` | 否 | 评审结论（APPROVE/REJECT/PASS/FAIL） |
| `summary` | 是 | 一句话摘要 |

### 消息类型

| 类型 | 方向 | 说明 |
|------|------|------|
| `task_assign` | scheduler → agent | 分配任务 |
| `task_result` | agent → scheduler | 返回执行结果 |
| `review_request` | scheduler → review-agent | 请求评审 |
| `review_result` | review-agent → scheduler | 评审结果 |
| `gate_result` | gate-auto → scheduler | L1 闸门结果 |
| `contract_update` | architect → all | API 契约变更通知 |
| `contract_change_request` | dev-agent → architect | 请求变更 API 契约（DevAgent 发现需要改契约时） |
| `barrier_wait` | scheduler → scheduler | 同步屏障等待（内部状态，记录哪些 Agent 未完成） |
| `barrier_released` | scheduler → test-agent | 屏障释放，启动下游 Agent |
| `retry` | scheduler → agent | 重试通知 |
| `escalate` | any → human | 升级到人类 |

---

## 2. 当前任务

| Task ID | 名称 | 状态 | 分配给 | Round | 更新时间 |
|---------|------|------|--------|-------|---------|
| - | （暂无活跃任务） | - | - | - | - |

---

## 3. 消息日志（Trace）

> 记录 Agent 间消息传递，用于可观测性和调试。

| msg_id | 时间 | From | To | 类型 | Task ID | 摘要 | Verdict |
|--------|------|------|----|------|---------|------|---------|
| - | - | - | - | - | - | - | - |

---

## 4. 架构决策记录

> ArchitectAgent 的关键决策记录。

| 日期 | 决策 | 理由 | 影响 | Artifact |
|------|------|------|------|---------|
| 2026-08-10 | 采用三层融合架构 | SDD+Multi-Agent+Loop 各取所长 | 全局架构 | - |
| 2026-08-10 | 采用混合模式调度器 | 确定性骨架+LLM嵌入点，可控又灵活 | 调度器设计 | `agents/orchestrator.md` |

---

## 5. 评审记录

| Task ID | Round | 评审结果 | Critical | Warning | 评审时间 | Artifact |
|---------|-------|---------|---------|---------|---------|---------|
| - | - | - | - | - | - | - |

---

## 6. 构建/部署记录

| Task ID | 构建结果 | 部署结果 | 环境 | 时间 | Artifact |
|---------|---------|---------|------|------|---------|
| - | - | - | - | - | - |

---

## 7. 使用规则

> **H-2 修复：Blackboard 维护责任明确**。调度器是 Blackboard 的唯一写入者，Agent 不直接写 Blackboard。Agent 输出的结构化 JSON 由调度器落盘到 artifact store 后，调度器代写对应的 Blackboard 消息。这符合 `orchestrator.md` §1.2 "Agent 不保存主状态"原则。
>
> **M-3 修复（Phase 8.2）**：调度器主循环伪代码（`orchestrator.md` §3.1）已包含 `write_blackboard()` 调用，覆盖以下时机：
> - `DISPATCHED` 分支后：写入 `agent_dispatched` 消息（含 task_id / agent / round / phase / subtask / timestamp）
> - `AWAITING_AUTO_GATE` 失败时：写入 `gate_failed` 消息（含 failures 详情）
> - `AWAITING_REVIEW` APPROVE 时：写入 `review_approved` 消息
> - `AWAITING_REVIEW` REJECT 时：写入 `review_rejected` 消息（含 issues 详情）
> - `§9.5` 契约变更流程：DevAgent 输出 JSON 含 `contract_change_request` 字段时，调度器代写该消息并触发 ArchitectAgent 评估

- Agent 间**不直接通信**，所有消息通过 blackboard 中转
- 消息只存**摘要和 artifact 引用**，不存完整内容
- 消息是**追加式**日志，不修改历史消息
- **调度器负责写入所有消息**（包括 task_assign / agent_dispatched / retry / escalate / task_result / review_result / review_approved / review_rejected / gate_result / gate_failed / contract_update / contract_change_request / barrier_wait / barrier_released）
- Agent 输出结构化 JSON 后，调度器校验通过并落盘到 artifact store，然后代写对应 `task_result` / `review_result` / `gate_result` 消息到 Blackboard
- **Agent 不直接写 Blackboard**：Agent 的职责是输出结构化 JSON，Blackboard 消息由调度器根据 JSON 内容生成
- 契约变更请求（`contract_change_request`）也由调度器代写：DevAgent 在输出 JSON 中声明 `contract_change_request` 字段，调度器检测到后代写消息并触发 `orchestrator.md` §9.4 的契约变更流程
