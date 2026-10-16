# 多 Agent 协作系统全面审计报告

> 审计日期: 2026-08-13  
> 审计范围: Loop-WorkSpace Multi-Agent 开发系统全链路  
> 审计基准: 当前已实现的全部文件（agents/ + .loop/ + shared/ + doc/）

---

## 目录

1. [Agent 角色定义与编排器](#1-agent-角色定义与编排器)
2. [Agent 之间的通信协议](#2-agent-之间的通信协议)
3. [终止条件](#3-终止条件)
4. [错误处理机制和重试机制](#4-错误处理机制和重试机制)
5. [可观测性](#5-可观测性)
6. [上下文管理与会话机制](#6-上下文管理与会话机制)
7. [Memory 系统](#7-memory-系统)
8. [安全权限系统](#8-安全权限系统)
9. [自我评估体系](#9-自我评估体系)
10. [总结：缺口与建议](#10-总结缺口与建议)

---

## 1. Agent 角色定义与编排器

### 1.1 编排器（Orchestrator）

**文件**: `agents/orchestrator.md`

**定位**: 不是常驻 LLM Agent，而是**确定性调度系统 + 嵌入式 LLM 决策点**。

| 属性 | 说明 |
|------|------|
| 本质 | 确定性 Workflow Engine（代码逻辑）+ 8 个 LLM 嵌入点 |
| 核心原则 | Agent 不保存主状态；任务状态在调度器；上下文在 artifact store；交接靠结构化产物 |
| 状态驱动 | 状态机引擎读取任务卡 `status` 字段决定下一步（查表，不允许 LLM 跳转） |
| 组件 | 状态机引擎 + 闸门执行器 + Agent 调用器 + 产物管理器 + 重试/回滚控制器 + 运行时上下文 |

**调度器主循环**（`orchestrator.md` §3.1）:

```
PENDING → DISPATCHED → RUNNING → AWAITING_AUTO_GATE → AWAITING_REVIEW → APPROVED → DONE
                                                                  ↘ REJECTED → RETRY ↗
                  APPROVED → ENV_CHECK → DISPATCHED（开发阶段）
```

**8 个 LLM 嵌入点**:

| # | 节点 | 触发状态 | 调用 Agent | 决策范围 |
|---|------|---------|-----------|---------|
| 1 | 需求拆解 | PENDING | SpecAgent | 怎么拆、Done 项怎么定 |
| 2 | 任务分配 | PENDING | Orchestrator-LLM | 谁来做、怎么并行 |
| 3 | 架构决策 | DISPATCHED | ArchitectAgent | 技术选型、契约定义 |
| 4 | UI 设计 | DISPATCHED | UIDesignAgent | 页面/组件/交互/tokens |
| 5 | 代码实现 | DISPATCHED | Backend/Frontend/ClientDev | 怎么实现（按项目类型） |
| 6 | 测试 | DISPATCHED | TestAgent | 测试怎么写、覆盖率 |
| 7 | 评审 | AWAITING_REVIEW | ReviewAgent | Approve/Reject + issues |
| 8 | 部署 | PENDING→DISPATCHED | DevOpsAgent | 构建参数、部署目标 |

### 1.2 Agent 清单（10 个 + Gate-Auto）

| Agent | 文件 | 优先级 | 触发状态 | 产物 | 并行能力 | 上下文预算 (in/out) | 模型温度 |
|-------|------|--------|---------|------|---------|---------------------|---------|
| SpecAgent | spec-agent.md | P0 | PENDING | task-xxx.md + spec.json | ❌ | 8K/6K | 0.5 |
| Orchestrator-LLM | orchestrator.md | P0 | PENDING | assignment.json | ❌ | 30K | - |
| ArchitectAgent | architect-agent.md | P1 | DISPATCHED | architecture.json + api-contract.md | ❌ | 10K/8K | 0.4 |
| UIDesignAgent | ui-design-agent.md | P1 | DISPATCHED | ui-design.json | ✅ (与 Backend 并行) | 15K/12K | 0.5 |
| BackendDevAgent | backend-dev.md | P1 | DISPATCHED | backend.diff | ✅ | 20K/16K | 0.2 |
| FrontendDevAgent | frontend-dev.md | P1 | DISPATCHED | frontend.diff | ✅ (后半程) | 20K/16K | 0.3 |
| ClientDevAgent | client-dev.md | P1 | DISPATCHED | client.diff | ✅ (与 Backend 并行) | 20K/16K | 0.2 |
| TestAgent | test-agent.md | P2 | DISPATCHED | test-report.json | ❌ | 15K/10K | 0.2 |
| ReviewAgent | review-agent.md | P2 | AWAITING_REVIEW | review.json | ❌ | 15K/10K | 0.1 |
| DevOpsAgent | devops-agent.md | P3 | PENDING | deploy-result.json | ❌ | 8K/6K | 0.1 |
| Gate-Auto | gate-auto.md | - | AWAITING_AUTO_GATE / ENV_CHECK | gate-result.json / env-check-result.json | ❌ | N/A (确定性脚本) | N/A |

### 1.3 Agent 统一模板（v2.0）

每个 Agent 遵循固定结构: `角色定位 → 调用约定 → 输入 Schema → 输出 Schema → 能力声明 → 上下文预算 → 失败策略 → 行为约束 → 产物输出 → 协作关系`

**核心理念**: Agent = 可调用能力，不是常驻角色。调度器按需新开 LLM 会话，执行完返回结构化 JSON，会话即销毁。

### 1.4 审计结论

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 编排器定义完整 | ✅ | 确定性骨架 + LLM 嵌入点混合模式，8 个嵌入点覆盖全链路 |
| Agent 角色清晰 | ✅ | 10 个 Agent + Gate-Auto，每个有固定 IO Schema、预算、失败策略 |
| Agent 优先级分明 | ✅ | P0(系统骨架) → P1(核心开发) → P2(质量保障) → P3(运维) |
| 项目类型区分 | ✅ | WEB→FrontendDev，QT→ClientDev，通过 project_type 字段路由 |
| Agent 不保存状态 | ✅ | LLM 无状态，所有状态在调度器侧，重要信息落盘 artifact store |

---

## 2. Agent 之间的通信协议

### 2.1 通信拓扑: 星型（黑板模式）

**文件**: `shared/blackboard.md`

**核心规则**: Agent 间**不直接通信**，所有消息通过 blackboard 中转。

```
        ┌───────────┐
        │ Scheduler  │
        └─────┬──────┘
              │ task_assign / retry / escalate
    ┌─────────┼─────────┐
    ▼         ▼         ▼
 Agent A   Agent B   Agent C
    │         │         │
    └─────────┴─────────┘
              │ task_result / review_result
              ▼
        ┌───────────┐
        │ Blackboard │
        └───────────┘
```

### 2.2 消息 Schema

每条消息必须符合结构化 JSON:

```json
{
  "msg_id": "msg-001",
  "timestamp": "ISO 8601",
  "from": "scheduler|agent-name",
  "to": "scheduler|agent-name",
  "type": "task_assign|task_result|review_request|review_result|gate_result|contract_update|contract_change_request|barrier_wait|barrier_released|retry|escalate",
  "task_id": "task-NNN",
  "round": N,
  "artifacts": [{"type": "...", "path": "...", "description": "..."}],
  "verdict": "APPROVE|REJECT|PASS|FAIL|null",
  "summary": "一句话摘要"
}
```

### 2.3 10 种消息类型

| 类型 | 方向 | 用途 |
|------|------|------|
| `task_assign` | scheduler → agent | 分配任务 |
| `task_result` | agent → scheduler | 返回执行结果 |
| `review_request` | scheduler → review-agent | 请求评审 |
| `review_result` | review-agent → scheduler | 评审结果 |
| `gate_result` | gate-auto → scheduler | L1 闸门结果 |
| `contract_update` | architect → all | API 契约变更通知 |
| `contract_change_request` | dev-agent → architect | 请求变更 API 契约 |
| `barrier_wait` | scheduler → scheduler | 同步屏障等待（内部状态） |
| `barrier_released` | scheduler → test-agent | 屏障释放，启动下游 |
| `retry` | scheduler → agent | 重试通知 |
| `escalate` | any → human | 升级到人类 |

### 2.4 交接规则

| 规则 | 说明 |
|------|------|
| 传引用不传内容 | 交接只传 artifact 路径，下游 Agent 按需读取 |
| 结构化 JSON | 所有 Agent 输出必须符合 `agents/_schema.md` Schema |
| 追加式日志 | Blackboard 消息不修改历史，只追加 |
| 摘要 + 引用 | 消息只存摘要和 artifact 路径，不存完整内容 |

### 2.5 同步屏障机制（`orchestrator.md` §9.3）

6 个屏障点确保并行 Agent 完成后才启动下游:

| 屏障 | 等待产物 | 释放后启动 |
|------|---------|-----------|
| Barrier-1 | architecture.json + api-contract.md | UIDesignAgent + BackendDevAgent |
| Barrier-2 | ui-design.json | FrontendDev/ClientDev |
| Barrier-3 | backend.diff + frontend.diff/client.diff | TestAgent |
| Barrier-4 | test-report.json | Gate-Auto |
| Barrier-5 | gate-result.json (PASS) | ReviewAgent |
| Barrier-6 | review.json (APPROVE) | DevOpsAgent |

### 2.6 审计结论

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 通信协议定义 | ✅ | 10 种消息类型，结构化 JSON Schema |
| 黑板模式 | ✅ | Agent 不直接通信，通过 blackboard 中转 |
| 同步屏障 | ✅ | 6 个屏障点，确定性代码管理，不依赖 LLM 判断 |
| 交接规则 | ✅ | 传引用不传内容，结构化产物 |
| API 契约变更流程 | ✅ | DevAgent 发请求 → ArchitectAgent 审批 → 通知 all → 受影响产物作废 → RETRY |
| **GAP: 消息持久化** | ⚠️ | Blackboard 消息日志目前是 Markdown 表格，非结构化存储；大规模运行时需考虑数据库 |

---

## 3. 终止条件

### 3.1 终态定义（`state-machine.md` §1）

三个终态，不可再跳转:

| 终态 | 含义 | 触发条件 |
|------|------|---------|
| `DONE` | 任务完成 | 所有 Done 项 passed + 9 产物齐全 + Agent 覆盖率满足 + verdict=APPROVE + deploy.success=true |
| `FAILED` | 任务失败 | 预算耗尽 / 超时 |
| `ESCALATED` | 升级到人类 | 重试耗尽 / 连续失败 3 次 |

### 3.2 收敛策略（`loop.md` §5）

```yaml
convergence:
  strategy: all_done              # all_done | threshold
  done_threshold: 1.0            # threshold 模式下生效
  require_L1_pass: true          # 必须 L1 闸门通过
  require_L2_approve: true       # 必须 L2 评审通过
```

### 3.3 终止触发条件

| 条件 | 动作 | 状态 |
|------|------|------|
| 所有 Done 项勾选 + L1 pass + L2 approve | 归档到 done/ | DONE |
| 达到最大轮次 (max_rounds=10) | 升级 | ESCALATED |
| 预算耗尽 (total_max=800000 tokens) | 升级 | FAILED |
| 超时 (total_max_seconds=4800s=80min) | 升级 | FAILED |
| 连续失败 3 次 | 升级 | ESCALATED |
| L3 人工 REJECT / 超时 24h | 升级 | ESCALATED |
| ENV_CHECK 失败 | 等待人类 | AWAITING_HUMAN (非终态) |

### 3.4 无限循环防护

| 机制 | 说明 |
|------|------|
| max_rounds=10 | 硬性循环上限 |
| max_retry=3 | 每轮重试上限 |
| consecutive_fail=3 | 连续失败上限 |
| token total_max=800000 | 预算硬性上限 |
| time total_max_seconds=4800 | 时间硬性上限 |
| worktree_isolation=false | 首期同步模式，避免并行回滚冲突 |

### 3.5 审计结论

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 终态定义清晰 | ✅ | DONE/FAILED/ESCALATED 三终态，不可再跳转 |
| 收敛策略 | ✅ | all_done 模式，必须 L1+L2 全通过 |
| 无限循环防护 | ✅ | 5 层防护（轮次/重试/连续失败/预算/时间） |
| 非终态暂停 | ✅ | AWAITING_HUMAN 可恢复（pre_awaiting_human_status） |
| **GAP: 多任务收敛** | ⚠️ | 多子任务的 PENDING→APPROVED→PENDING 循环语义已定义但未实际验证 |

---

## 4. 错误处理机制和重试机制

### 4.1 三层质检体系

| 层 | 执行者 | 触发时机 | 不通过动作 | 类型 |
|----|--------|---------|-----------|------|
| **L1 自动闸门** | Gate-Auto (确定性脚本) | Agent 输出产物后 | RETRY (回滚 checkpoint) | 声明式 check |
| **L2 契约评审** | ReviewAgent (LLM) | L1 通过后 | REJECTED → RETRY 或 ESCALATED | LLM 评审 |
| **L3 人工抽查** | 开发者 | 关键节点 | ESCALATED 等待人类 | 人工 |

### 4.2 L3 人工抽查触发条件

```yaml
L3_human_inspection:
  trigger_conditions:
    - architecture_change       # 架构变更
    - ui_design_change         # UI 设计变更
    - security_related         # 安全相关
    - retry_exhausted          # 重试耗尽前最后一次
    - budget_near_limit        # 预算接近上限
    - review_l3_hints          # ReviewAgent 输出 l3_trigger_hints 非空
```

### 4.3 重试策略（`loop.md` §3）

```yaml
retry_policy:
  max_retry: 3
  retry_on:
    - L1_gate_fail
    - L2_review_reject
    - agent_timeout
    - output_schema_violation
  rollback:
    method: git_checkpoint
    on_retry:
      - git reset --hard {checkpoint}
      - clear_artifacts_for_agent  # 选择性回滚
      - retry_count += 1
      - status → DISPATCHED
  escalate_after:
    max_retry_exhausted: true
    consecutive_fail: 3
    budget_exhausted: true
```

### 4.4 选择性回滚（`state-machine.md` §5）

```python
function clear_artifacts_for_agent(task_id, round, agent_name, project):
    # 影响全局的 Agent (architect/review/devops/gate) 失败 → 全清当前 round
    # 局部 Agent (backend/frontend/client/ui-design/test) 失败 → 仅清对应产物
```

| Agent 失败 | 回滚范围 |
|-----------|---------|
| backend-dev | 仅清 backend.diff |
| frontend-dev | 仅清 frontend.diff |
| client-dev | 仅清 client.diff |
| ui-design-agent | 仅清 ui-design.json |
| test-agent | 仅清 test-report.json |
| architect-agent | 全清当前 round |
| review-agent | 全清当前 round |
| devops-agent | 全清当前 round |
| gate-auto | 全清当前 round |

### 4.5 每个 Agent 的失败策略

| Agent | Schema 不符 | L1 失败 | L2 拒绝 | 超时 | Fallback |
|-------|------------|---------|---------|------|---------|
| SpecAgent | 重试 2 次 | N/A | N/A | ESCALATED | 升级 Orchestrator-LLM 追问用户 |
| ArchitectAgent | 重试 2 次 | N/A | N/A | ESCALATED | 升级人类 |
| UIDesignAgent | 重试 2 次 | 回滚 RETRY(3) | 回滚 RETRY(3) | ESCALATED | 升级 Orchestrator-LLM |
| BackendDev | 重试 2 次 | 回滚 RETRY(3) | 回滚 RETRY(3) | ESCALATED | 升级 Orchestrator-LLM |
| FrontendDev | 重试 2 次 | 回滚 RETRY(3) | 回滚 RETRY(3) | ESCALATED | 升级 Orchestrator-LLM |
| ClientDev | 重试 2 次 | 回滚 RETRY(3) | 回滚 RETRY(3) | ESCALATED | 升级 Orchestrator-LLM |
| TestAgent | 重试 2 次 | N/A | N/A | ESCALATED | 回退 DevAgent 修复 |
| ReviewAgent | 重试 2 次 | N/A | N/A | ESCALATED | 升级人类审核 |
| DevOpsAgent | 重试 2 次 | N/A | N/A | ESCALATED | 升级人类 + 完整日志 |
| Gate-Auto | N/A | N/A | N/A | 整体 FAIL | N/A |

### 4.6 ENV_CHECK 错误处理

| 场景 | verdict | next_action | 状态转换 |
|------|---------|-------------|---------|
| 全部 pass | PASS | DISPATCHED | ENV_CHECK → DISPATCHED |
| 必装 pass + 可选 warn | PASS | DISPATCHED | ENV_CHECK → DISPATCHED |
| 必装有 fail | FAIL | AWAITING_HUMAN | ENV_CHECK → AWAITING_HUMAN |
| check_cmd 超时 | 该项 fail/warn | 按上述规则 | 同上 |

**关键差异**: ENV_CHECK 失败走 AWAITING_HUMAN（不 RETRY），因为环境不匹配重试结果不变。

### 4.7 审计结论

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 三层质检体系 | ✅ | L1(自动) + L2(LLM) + L3(人工) 分层防御 |
| 重试策略 | ✅ | max_retry=3，按失败类型区分回滚范围 |
| 选择性回滚 | ✅ | 局部 Agent 仅清自身产物，全局 Agent 全清 |
| Agent 级失败策略 | ✅ | 每个 Agent 有独立的失败/重试/fallback 策略 |
| ENV_CHECK 特殊处理 | ✅ | 环境问题不重试，直接 AWAITING_HUMAN |
| 硬性强制原则 | ✅ | 不满足条件不接受下一阶段，Layer 0/1/2 全适用 |
| **GAP: 幻觉检测** | ⚠️ | 无 LLM 幻觉检测机制（如 Agent 输出不符事实的代码） |
| **GAP: 死锁检测** | ⚠️ | 并行屏障等待无超时检测（Barrier 未释放时无自动升级） |

---

## 5. 可观测性

### 5.1 状态查询接口（`state-machine.md` §6）

```
GET task/{task_id}/status
→ {
    "task_id": "task-001",
    "status": "AWAITING_REVIEW",
    "assigned_to": "review-agent",
    "round": 2,
    "retry_count": 1,
    "history": [{"from": "PENDING", "to": "DISPATCHED", "timestamp": "..."}]
  }
```

### 5.2 可观测性数据源

| 数据源 | 文件/位置 | 内容 |
|--------|----------|------|
| 任务卡 frontmatter | `.loop/active/{project}/task-NNN.md` | status/assigned_to/round/retry_count/executed_agents/artifacts |
| Artifact store | `.loop/artifacts/{project}/{task_id}/round-N/` | 所有 Agent 产物 JSON（spec/architecture/ui-design/diff/test/gate/review/deploy） |
| Blackboard trace | `shared/blackboard.md` §3 | 消息日志（msg_id/timestamp/from/to/type/summary） |
| Gate 结果 | gate-result.json | verdict/total_checks/passed/failed/results[] |
| ENV_CHECK 结果 | env-check-result.json | verdict/results[]/upgrade_suggestions[] |
| Review 报告 | review.json | verdict/issues[]/l3_trigger_hints[] |
| 状态转换日志 | `log_transition()` 输出 | from→to/artifacts_verified/agents_verified |
| 违规日志 | `on_violation` 输出 | 缺失产物列表 + 缺失 Agent 列表 + 失败 Done 项 |

### 5.3 Blackboard 多维度记录

| 板块 | 内容 |
|------|------|
| 当前任务 | Task ID/名称/状态/分配给/Round/更新时间 |
| 消息日志 (Trace) | 全部 Agent 间消息传递记录 |
| 架构决策记录 | ArchitectAgent 的关键决策 + 理由 + 影响 |
| 评审记录 | Task ID/Round/评审结果/Critical/Warning |
| 构建/部署记录 | Task ID/构建结果/部署结果/环境/时间 |

### 5.4 审计结论

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 状态查询 API | ✅ | 已定义 `GET task/{task_id}/status` 接口 |
| 全链路追踪 | ✅ | Blackboard trace 记录全部 Agent 间消息 |
| 产物可追溯 | ✅ | Artifact store 按项目/任务/轮次层级存储 |
| 违规日志 | ✅ | `on_violation: reject_transition` 记录缺失列表 |
| 多维度记录 | ✅ | 任务/消息/架构/评审/部署 5 个维度 |
| **GAP: 实时监控** | ❌ | 无实时 Dashboard / metrics 采集 / alerting |
| **GAP: 性能监控** | ❌ | 无 Agent 执行时间/Token 消耗/成本统计的实时展示 |
| **GAP: 调试回放** | ❌ | 无法从历史 trace 回放某次任务的完整执行过程 |
| **GAP: 结构化存储** | ⚠️ | Blackboard 当前是 Markdown 表格，非数据库；大规模运行需结构化存储 |

---

## 6. 上下文管理与会话机制

### 6.1 会话隔离原则

**文件**: `agents/orchestrator.md` §4, `agents/_schema.md` §1

| 原则 | 说明 |
|------|------|
| 每个嵌入点新开独立会话 | 不继承前一会话的上下文窗口 |
| 会话结束立即销毁 | LLM 无状态，重要信息已落盘 |
| 交接传引用不传内容 | artifact 路径列表，非完整内容 |

### 6.2 上下文包构造

调度器构造的上下文包注入到 Agent 会话:

```json
{
  "agent_definition": "agents/{agent_name}.md",     // Agent 角色定义（system prompt）
  "task": {
    "card_path": ".loop/active/{project}/task-xxx.md",
    "id": "task-xxx",
    "goal": "任务目标摘要",
    "constraints": ["约束1", "约束2"],
    "done_items": [{"id": "d1", "check": "...", "expect": "...", "status": "pending"}]
  },
  "context_refs": [
    {"type": "artifact", "path": ".loop/artifacts/...", "description": "上游产物"},
    {"type": "shared", "path": "shared/api-contract.md", "description": "API 契约"},
    {"type": "blackboard", "path": "shared/blackboard.md", "description": "共享黑板摘要"}
  ],
  "output_schema": "agents/_schema.md#{agent_name}-output",
  "acceptance_criteria": "输出必须符合 Schema，否则触发重试"
}
```

### 6.3 上下文层级

| 层级 | 文件 | 作用域 | 生命周期 |
|------|------|--------|---------|
| 项目级 | `.loop/context.md` | 全局（所有 Agent 共享） | 长期 |
| 产品级 | `projects/{product}/prd.md` | 单产品 | 长期 |
| 任务级 | `.loop/active/{project}/task-NNN.md` | 单任务 | 完成后归档 |
| 轮次级 | `.loop/artifacts/{project}/{task_id}/round-N/` | 单轮次 | RETRY 时清除 |
| 全局产物 | round-0 目录 | 跨轮次（spec.json + assignment.json） | 整个任务 |

### 6.4 Token 预算管理

```yaml
token_budget:
  orchestrator_llm: 30000
  spec_agent: 30000
  architect: 40000
  ui_design_agent: 30000
  backend_dev: 100000      # 代码 Agent 预算最大
  frontend_dev: 100000
  client_dev: 100000
  test_agent: 30000
  review_agent: 20000
  devops_agent: 20000
  total_max: 800000         # 单任务总上限（留 60% 余量给 1-2 次重试）

time_budget:
  per_agent_seconds: 600   # 单 Agent 最大执行时间
  gate_timeout_seconds: 300
  total_max_seconds: 4800  # 80 分钟，覆盖完整串行链路
```

### 6.5 预算守卫

每轮循环结束后检查:
- Token 消耗是否超过 `total_max`
- 时间是否超过 `total_max_seconds`
- 轮次是否超过 `max_rounds`

任一超限 → `transition_to(ESCALATED)`，附带消耗报告。

### 6.6 审计结论

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 会话隔离 | ✅ | 每个嵌入点独立会话，结束即销毁 |
| 上下文包构造 | ✅ | agent_definition + task + context_refs + output_schema |
| 交接传引用 | ✅ | artifact 路径列表，不传内容 |
| 上下文层级 | ✅ | 项目/产品/任务/轮次 4 级，加 round-0 全局产物 |
| Token 预算 | ✅ | 每 Agent 独立预算 + 总预算 800K + 预算守卫 |
| 时间预算 | ✅ | 每 Agent 600s + 闸门 300s + 总计 4800s |
| **GAP: 上下文压缩** | ⚠️ | 无上下文窗口溢出时的压缩/摘要策略 |
| **GAP: 跨任务上下文** | ⚠️ | 多子任务时 executed_agents 累积但上下文引用未定义复用策略 |

---

## 7. Memory 系统

### 7.1 现有"记忆"机制

| 机制 | 文件 | 类型 | 说明 |
|------|------|------|------|
| Artifact store | `.loop/artifacts/` | 产物存储 | Agent 输出的结构化 JSON，按项目/任务/轮次隔离 |
| Blackboard | `shared/blackboard.md` | 共享状态空间 | 消息日志、架构决策、评审记录、部署记录 |
| Context | `.loop/context.md` | 项目上下文 | 技术栈、开发约定、当前阶段 |
| API 契约 | `shared/api-contract.md` | 单一事实来源 | 前后端 API 契约定义 |
| 任务卡归档 | `.loop/done/{project}/` | 历史记录 | 完成任务的只读归档 |
| Working Memory | `.codebuddy/memory/` | 跨会话 AI 记忆 | AI 助手的持久记忆（非 Agent 系统组件） |

### 7.2 记忆机制分析

| 维度 | 现状 | 评价 |
|------|------|------|
| **短期记忆（会话内）** | LLM 上下文窗口 | ✅ 有（但会话结束即销毁） |
| **中期记忆（任务内）** | Artifact store + 任务卡 frontmatter | ✅ 有（按轮次存储） |
| **长期记忆（跨任务）** | Blackboard + context.md + 归档任务卡 | ⚠️ 部分（有存储无检索） |
| **经验记忆（跨项目）** | 无 | ❌ 缺失 |
| **学习反馈** | 无 | ❌ 缺失 |

### 7.3 审计结论

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 产物持久化 | ✅ | Artifact store 按项目/任务/轮次层级存储 |
| 共享状态空间 | ✅ | Blackboard 提供消息日志和决策记录 |
| 项目上下文 | ✅ | context.md 提供全局技术栈和约定 |
| 历史归档 | ✅ | done/ 目录保留完成任务卡 |
| **GAP: 经验积累** | ❌ | 无机制从历史任务中提取经验用于未来任务规划 |
| **GAP: 跨项目知识转移** | ❌ | 不同项目间无知识共享机制 |
| **GAP: 学习反馈环** | ❌ | 无"任务完成后总结经验→更新策略→指导下一次"的闭环 |
| **GAP: 语义检索** | ❌ | 无法按语义检索历史 artifact 或任务卡 |
| **GAP: Agent 个性化记忆** | ❌ | 每个 Agent 无独立的记忆库（如"上次犯过的错误不再犯"） |

**总体评价**: 当前系统的"记忆"本质上是**文件系统存储**，不是智能记忆系统。有存储无检索、有历史无学习。

---

## 8. 安全权限系统

### 8.1 现有安全机制

| 机制 | 文件 | 说明 |
|------|------|------|
| ReviewAgent 安全审查 | `agents/review-agent.md` | 检查注入防护、XSS 防护、敏感信息泄露 |
| 安全一票否决 | `agents/review-agent.md` | 安全问题直接 REJECT，severity: critical |
| API 契约控制变更 | `shared/api-contract.md` + `orchestrator.md` §9.4 | DevAgent 不允许自行修改契约，需 ArchitectAgent 审批 |
| 敏感信息保护 | `agents/devops-agent.md` | 密码/密钥不写入日志 |
| 生产部署人工确认 | `agents/devops-agent.md` | 生产环境部署必须 L3 人工确认 |
| L3 人工监督 | `agents/orchestrator.md` §5.3 | 架构变更/安全相关/重试耗尽时升级人类 |
| check_cmd 平台感知 | `agents/architect-agent.md` | Windows 用 `py --version` 避免 Python 2.7 误命中 |
| ENV_CHECK | `agents/gate-auto.md` §11 | 开发环境预检，必装项缺失直接 FAIL |

### 8.2 安全机制分析

| 维度 | 现状 | 评价 |
|------|------|------|
| **Agent 间信任边界** | 星型拓扑，Agent 不直接通信 | ✅ 有（通过 blackboard 隔离） |
| **敏感信息隔离** | DevOpsAgent 不记日志 | ⚠️ 部分（仅 DevOpsAgent 声明，其他 Agent 无约束） |
| **审计日志** | Blackboard trace | ⚠️ 部分（有消息日志，无安全审计日志） |
| **人类监督 (HITL)** | L3 人工抽查 + AWAITING_HUMAN | ✅ 有 |
| **权限控制 (RBAC)** | 无 | ❌ 缺失 |
| **沙箱隔离** | worktree_isolation=false | ❌ 已禁用 |
| **数据隔离** | 项目级 artifact 隔离 | ⚠️ 部分（仅 artifact 按项目隔离，Agent 无隔离） |
| **工具权限** | 每个 Agent 声明 tools 列表 | ⚠️ 部分（声明但不强制，无运行时权限检查） |

### 8.3 审计结论

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 安全审查 | ✅ | ReviewAgent 检查注入/XSS/敏感信息，安全一票否决 |
| 契约控制变更 | ✅ | API 契约变更需 ArchitectAgent 审批 + 通知 all |
| 人工监督 | ✅ | L3 抽查 + AWAITING_HUMAN + 生产部署人工确认 |
| Agent 信任边界 | ✅ | 星型拓扑，blackboard 中转，不直接通信 |
| **GAP: RBAC 权限模型** | ❌ | 无 Agent 级权限控制（如 Agent A 不能修改 Agent B 的产物） |
| **GAP: 沙箱隔离** | ❌ | worktree_isolation=false，Agent 在同一工作目录操作 |
| **GAP: 安全审计日志** | ❌ | 无独立的安全审计日志（谁/何时/做了什么/安全相关） |
| **GAP: 敏感信息全局策略** | ❌ | 仅 DevOpsAgent 声明不记日志，其他 Agent 无约束 |
| **GAP: 工具权限强制** | ❌ | Agent 声明 tools 列表但无运行时权限检查 |
| **GAP: 密钥管理** | ❌ | 无密钥/凭据管理系统（API key、DB password 等） |

**总体评价**: 安全机制依赖 ReviewAgent 的 LLM 审查和 L3 人工抽查，缺乏系统级的权限控制、沙箱隔离和审计体系。适合个人开发者场景，不适合多团队/生产级场景。

---

## 9. 自我评估体系

### 9.1 现有评估机制

| 机制 | 文件 | 类型 | 说明 |
|------|------|------|------|
| L1 自动闸门 | `agents/gate-auto.md` | 声明式检查 | 执行任务卡 Done 项的 check 命令，pass/fail 判定 |
| L2 契约评审 | `agents/review-agent.md` | LLM 评审 | 代码质量/安全/规范/测试覆盖/Done 条件审查 |
| L3 人工抽查 | `agents/orchestrator.md` §5.3 | 人工 | 架构变更/UI 变更/安全相关/重试耗尽/预算接近上限 |
| ENV_CHECK | `agents/gate-auto.md` §11 | 环境预检 | 开发工具链版本检测，必装项缺失 FAIL |
| Schema 校验 | `agents/_schema.md` §4 | 硬性强制 | Agent 输出必须符合 Schema，不符禁止状态转换 |
| Agent 覆盖率 | `state-machine.md` §5 | 硬性强制 | executed_agents 必须含全部必跑 Agent |
| Done 项校验 | `state-machine.md` §5 | 硬性强制 | 所有 Done 项 status 必须 passed |
| verdict 校验 | `state-machine.md` §5 | 硬性强制 | review.json verdict=APPROVE + deploy.success=true |
| 产物完整性 | `state-machine.md` §5 | 硬性强制 | 9 产物齐全才能 DONE |
| Layer 0 自检清单 | `layer-0-must-run.md` | 归档前 | 产物完整性 + Agent 覆盖率 + Done 项 + Schema 合规 |
| l3_trigger_hints | `agents/review-agent.md` | LLM 输出 | ReviewAgent 输出触发提示，调度器据此决定是否 L3 |

### 9.2 评估维度覆盖

| 维度 | 机制 | 状态 |
|------|------|------|
| 代码质量 | L2 ReviewAgent | ✅ |
| 安全合规 | L2 ReviewAgent + L3 人工 | ✅ |
| 契约一致性 | L2 ReviewAgent | ✅ |
| 测试覆盖率 | TestAgent + L2 ReviewAgent | ✅ |
| 环境就绪 | ENV_CHECK | ✅ |
| 构建成功 | L1 Gate-Auto (cmake build) | ✅ |
| 测试通过 | L1 Gate-Auto (ctest) | ✅ |
| 部署成功 | DevOpsAgent + verdict 校验 | ✅ |
| Agent 完整性 | Agent 覆盖率校验 | ✅ |
| **Agent 输出质量评分** | 无 | ❌ |
| **任务效率评估** | 无 | ❌ |
| **预算消耗分析** | 无 | ❌ |
| **历史趋势分析** | 无 | ❌ |
| **持续改进反馈** | 无 | ❌ |

### 9.3 审计结论

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 三层质检体系 | ✅ | L1(自动声明式) + L2(LLM 评审) + L3(人工) |
| 硬性强制 | ✅ | 6 项硬性校验（Schema/覆盖率/Done/verdict/产物/env_check） |
| ENV_CHECK | ✅ | 架构阶段环境预检，避免开发阶段才发现环境问题 |
| Layer 0 自检清单 | ✅ | 归档前逐项确认，任一未勾选禁止 DONE |
| l3_trigger_hints | ✅ | ReviewAgent 输出 L3 触发提示，调度器自动判定 |
| **GAP: 质量评分** | ❌ | 无 Agent 输出质量评分（如代码质量 0-100 分） |
| **GAP: 效率指标** | ❌ | 无任务执行效率指标（耗时/token 消耗/重试次数统计） |
| **GAP: 持续改进** | ❌ | 无"任务完成后总结→更新检查规则→指导下一次"的闭环 |
| **GAP: 回归基准** | ❌ | 无历史质量基线（如"上次覆盖率 85%，这次不能低于"） |
| **GAP: Agent 自评** | ❌ | Agent 不自评输出质量，依赖外部校验 |

**总体评价**: 自我评估体系在**质检和合规**维度完善（三层闸门 + 6 项硬性强制 + ENV_CHECK），但在**质量度量、效率分析、持续改进**维度缺失。是一个"通过/不通过"的二元评估系统，不是"好/坏/更好"的连续评估系统。

---

## 10. 总结：缺口与建议

### 10.1 各维度成熟度

| 维度 | 成熟度 | 评价 |
|------|--------|------|
| 1. Agent 角色与编排器 | ⭐⭐⭐⭐⭐ | 完整：10 Agent + 编排器 + 8 嵌入点 |
| 2. 通信协议 | ⭐⭐⭐⭐ | 完善：黑板模式 + 10 种消息 + 6 屏障 |
| 3. 终止条件 | ⭐⭐⭐⭐⭐ | 完整：3 终态 + 5 层防护 + 收敛策略 |
| 4. 错误处理 | ⭐⭐⭐⭐ | 完善：三层质检 + 选择性回滚 + ENV_CHECK |
| 5. 可观测性 | ⭐⭐⭐ | 中等：有数据源无实时监控 |
| 6. 上下文管理 | ⭐⭐⭐⭐ | 完善：会话隔离 + 上下文包 + 预算守卫 |
| 7. Memory 系统 | ⭐⭐ | 薄弱：有存储无学习 |
| 8. 安全权限 | ⭐⭐ | 薄弱：依赖 LLM 审查，无系统级安全 |
| 9. 自我评估 | ⭐⭐⭐ | 中等：质检完善，度量缺失 |

### 10.2 关键缺口汇总

| 优先级 | 缺口 | 维度 | 建议 |
|--------|------|------|------|
| 🔴 High | 无经验积累/学习反馈环 | Memory | 增加"任务完成后总结→更新策略"机制 |
| 🔴 High | 无 RBAC 权限模型 | 安全 | 增加 Agent 级权限控制（tools 白名单强制） |
| 🔴 High | 无实时监控/Dashboard | 可观测性 | 增加 metrics 采集 + 可视化面板 |
| 🟡 Medium | Blackboard 非结构化存储 | 通信/可观测性 | 大规模运行时迁移到数据库 |
| 🟡 Medium | 无上下文压缩策略 | 上下文管理 | 增加上下文窗口溢出时的摘要/截断策略 |
| 🟡 Medium | 无质量评分/效率指标 | 自我评估 | 增加 Agent 输出质量评分 + 任务效率统计 |
| 🟡 Medium | 无幻觉检测 | 错误处理 | 增加 LLM 输出事实性校验 |
| 🟡 Medium | 无屏障超时检测 | 错误处理 | Barrier 等待超时自动升级 |
| 🟢 Low | 无沙箱隔离 | 安全 | 后期并行模式启用 worktree 隔离 |
| 🟢 Low | 无密钥管理 | 安全 | 增加 secrets 管理（环境变量/vault） |
| 🟢 Low | 无调试回放 | 可观测性 | 从 trace 回放任务执行过程 |
| 🟢 Low | 无跨项目知识转移 | Memory | 增加跨项目经验检索 |

### 10.3 总体评价

当前系统是一个**面向个人开发者场景的轻量级 Multi-Agent 协作框架**，核心设计成熟度高：

**优势**:
- 确定性骨架 + LLM 嵌入点混合模式，可控又灵活
- 三层质检 + 6 项硬性强制，质量保障严密
- 项目级隔离 + ENV_CHECK，多项目并行就绪
- Agent 无状态 + artifact 外部化，可扩展性好

**待改进**:
- Memory 系统从"文件存储"升级为"智能记忆"
- 安全从"LLM 审查"升级为"系统级权限控制"
- 可观测性从"数据源"升级为"实时监控"
- 自我评估从"二元通过"升级为"质量度量 + 持续改进"

---

> 审计完成。本文档作为系统演进的基线参考，后续改进按 §10.2 优先级排序实施。
