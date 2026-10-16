# GraphFlow 系统运转模式总览

> 日期：2026-09-01（Mode A 清理版）
> 目的：总结当前 Loop Engineer / GraphFlow Runtime 架构下，用户输入、系统调度、多 Agent 通信、状态管理、产物周转与最终输出的完整链路，供人工审查完整性。

> **架构说明**：当前系统采用 **Mode A（SPEC 先行）** 模式——SpecAgent 在独立的 SPEC 层执行并产出不可变 Published SpecSnapshot，DRG 从 `spec_binding_node` 开始消费 Snapshot，不再在 DRG 内重新执行 SpecAgent。Mode B（spec_node 在 DRG 内执行 SpecAgent）的遗留节点与边已于 2026-09-01 清理完毕。

---

## 1. 总体流程

### 1.1 SPEC 层（DRG 之外）

```text
用户需求
  ↓
SpecAgent（独立执行 9 skill 固化）
  - PM Discussion 4 skill（create-prd 必选 + interview-script/summarize-interview/summarize-meeting 可选）
  - Discovery & Assumptions 4 skill（opportunity-solution-tree / identify-assumptions-new|existing / prioritize-assumptions，全部可选）
  - Job Stories 1 skill（job-stories，可选）
  ↓
ChangeSpec Draft（含 pm_discussion / discovery / assumptions / job_story 可选字段）
  ↓
Publish Gate（scheduler/spec_flow.py:validate_change_spec_payload）
  ↓
SpecSnapshot（15 字段 + content_hash，不可变）
  ↓
落入 specs/{project}/published/{spec_id}/v{version}/spec-snapshot.json
```

### 1.2 GraphFlow 层（DRG 执行）

```text
spec_binding_node（绑定 Snapshot 版本，校验 content_hash）
  ↓
planning_node（按 EARS 1:1 拆解 → execution-plan.json v1，planning_basis=requirement_ears）
  ↓
plan_gate_node（执行计划闸门：PASS / SPEC_MISMATCH / FAIL）
  ↓
architecture_impact_node（架构影响分析 → architecture.json）
  ↓
architecture_gate_node（架构闸门：PASS / FAIL）
  ↓
design_spec_node（生成 design-spec.json，含 component_boundaries / api_contracts / data_model / ui_contract）
  ↓
design_gate_node（设计规约闸门：PASS / FAIL；无 design-spec.json 时 auto-PASS）
  ↓
drg_compile_node（P1：若 rich design spec → refine_plan_from_design_spec → execution-plan.json v2，planning_basis=design_spec；重编译 DRG）
  ↓
implementation_drg_node（实施 DRG：N×{implement_node → test_node} 串行）
  ↓
final_gate_node（终态闸门：所有 DRG 节点完成）
  ↓
conformance_node（契约评审：trace_matrix + 三态 PASS/UNVERIFIED/FAIL）
  ↓
delivery_review_node（L2 语义评审：APPROVE / REJECT / L3_REVIEW_REQUIRED）
  ↓
deploy_node（部署）
  ↓
done_node（归档）
```

### 1.3 失败 / 暂停分支

```text
plan_gate=SPEC_MISMATCH / conformance=SPEC_MISMATCH
  -> awaiting_spec_change_node
  -> 用户提交 SpecChangeRequest
  -> 回到 spec_binding_node（重新绑定新版本 Snapshot）

任意 gate=FAIL / review=REJECT (retry_count < max_retry)
  -> retry_node（retry_policy.py 动态决定回到哪个节点）
  -> retry_count >= max_retry
  -> escalated_node（升级人工）

delivery_review=L3_REVIEW_REQUIRED
  -> awaiting_human_node（L3 抽查）
  -> human=APPROVE -> 回到 spec_binding_node（重新绑定，进入新一轮 DRG）
  -> human=REJECT or timeout -> escalated_node
```

---

## 2. GraphFlow 节点职责

| 节点 | 执行者 | 输入 | 输出 | 下一步 |
|---|---|---|---|---|
| `spec_binding_node` | Gate-Auto | Published SpecSnapshot | `spec-binding.json`（SpecRef 引用） | planning_node |
| `planning_node` | Orchestrator-LLM | SpecSnapshot、项目上下文 | `execution-plan.json`（v1，1:1 拆解 EARS） | plan_gate_node |
| `plan_gate_node` | Gate-Auto | execution-plan.json | `gate-result.json` | PASS -> architecture_impact；SPEC_MISMATCH -> awaiting_spec_change；FAIL -> retry |
| `architecture_impact_node` | ArchitectAgent | SpecSnapshot、execution-plan | `architecture.json`（impact + 必要时 5 intent docs） | architecture_gate_node |
| `architecture_gate_node` | Gate-Auto | architecture.json | `gate-result.json` | PASS -> design_spec；FAIL -> retry |
| `design_spec_node` | ArchitectAgent（二次调用） | architecture.json、SpecSnapshot | `design-spec.json`（component_boundaries / api_contracts / data_model / ui_contract / state_transitions / error_handling） | design_gate_node |
| `design_gate_node` | Gate-Auto | design-spec.json | `gate-result.json`（无 design-spec.json 时 auto-PASS） | PASS -> drg_compile；FAIL -> retry |
| `drg_compile_node` | Gate-Auto | execution-plan.json（v1 or v2）、design-spec.json | `execution-graph.json`（DRG 拓扑 + content_hash） | implementation_drg_node |
| `implementation_drg_node` | Orchestrator-LLM | execution-graph.json | 串行执行 N×{implement_node → test_node} | final_gate_node |
| `final_gate_node` | Gate-Auto | all_drg_nodes_done | `gate-result.json` | PASS -> conformance；FAIL -> retry |
| `conformance_node` | Conformance（确定性 + 语义） | 全部 Artifact、trace_matrix | `conformance.json`（PASS/UNVERIFIED/FAIL，priority-aware） | PASS -> delivery_review；SPEC_MISMATCH -> awaiting_spec_change |
| `awaiting_spec_change_node` | Human | 失败原因 + spec_diff | `spec-change-request.md` | 用户提交新 SpecSnapshot -> spec_binding_node |
| `delivery_review_node` | ReviewAgent | 代码、测试、契约、conformance | `review.json`（APPROVE/REJECT/L3_REVIEW_REQUIRED） | APPROVE -> deploy；REJECT -> retry；L3 -> awaiting_human |
| `deploy_node` | DevOpsAgent | 代码、构建配置 | `deploy-result.json` | success -> done |
| `done_node` | Scheduler | 全部产物 + 状态校验 | 任务归档 | 终态 |
| `retry_node` | Retry Policy | 失败节点 id、retry_count | reset_node + git reset | retry_count < max -> 回到失败节点；>= max -> escalated |
| `awaiting_human_node` | Human | L3 抽查请求 | 用户裁决 | APPROVE -> spec_binding_node；REJECT/timeout -> escalated |
| `escalated_node` | Human | 失败上下文 | 升级处理 | 终态 |

---

## 3. 多 Agent 协作链路

| 阶段 | Agent | 是否 LLM | 是否可跳过 | 主要产物 |
|---|---|---:|---:|---|
| Spec 生产（SPEC 层，DRG 之外） | SpecAgent | 是 | 否 | SpecSnapshot（含 PRD/OST/assumptions/job-stories 可选产物） |
| 规格绑定 | Gate-Auto | 否 | 否 | `spec-binding.json` |
| 执行计划编排 | Orchestrator-LLM | 是 | 否 | `execution-plan.json` |
| 架构影响分析 | ArchitectAgent | 是 | 否 | `architecture.json`、`documentation/*.md` |
| 设计规约生成 | ArchitectAgent（二次调用） | 是 | 否 | `design-spec.json` |
| DRG 编译 | Gate-Auto | 否 | 否 | `execution-graph.json` |
| 实施（每 WP） | Dev/Test/Gate/Review | 是 | 视项目而定 | `{wp}_implement` 产物 + `{wp}_test` 产物 |
| 契约评审 | Conformance（确定性）+ ReviewAgent（语义） | 混合 | 否 | `conformance.json` |
| 交付评审 | ReviewAgent | 是 | 否 | `review.json` |
| 部署 | DevOpsAgent | 是/工具化 | 否 | `deploy-result.json` |

---

## 4. Agent 通信模式

Agent 之间不直接对话。

```text
Agent A
  ↓ 输出结构化产物
Artifact Store
  ↓ 路径引用
Scheduler / ContextPackBuilder
  ↓ 注入下一个 Agent
Agent B
```

| 通信对象 | 机制 | 是否传全文 | 说明 |
|---|---|---:|---|
| Agent -> Scheduler | 结构化 JSON 输出 | 是，作为产物落盘 | Agent 执行结果 |
| Scheduler -> Agent | Context Pack | 否，主要传路径引用 | 降低上下文污染 |
| Agent -> Agent | Artifact 引用 | 否 | 不直接通信 |
| Scheduler -> Blackboard | 摘要消息 | 否 | 人类可读视图 |
| Scheduler -> Event Log | JSONL 事件 | 是，结构化事件 | 机器真源 |
| Memory -> Agent | `memory_refs` | 否，按需引用 | project brief / ADR / failure memory / recipes |

---

## 5. 状态管理

任务卡是当前任务状态来源，GraphFlow Runtime 是流程执行者，Artifact Store 是产物来源，Event Log 是运行轨迹来源。

| 状态 | 含义 | 谁推进 | 关键 guard |
|---|---|---|---|
| `PENDING` | 等待 spec_binding | Scheduler | SpecSnapshot 已发布 |
| `RUNNING` | DRG 节点执行中 | Executor | 当前节点必需产物已落盘 |
| `AWAITING_AUTO_GATE` | 等待 L1 自动闸门 | Gate-Auto | 当前 Agent 必需产物已落盘 |
| `AWAITING_REVIEW` | 等待 L2 评审 | ReviewAgent | `gate-result.json` PASS |
| `APPROVED` | L2 通过 | Scheduler | `review.json` APPROVE |
| `AWAITING_SPEC_CHANGE` | 等待规格变更 | Human | `pre_awaiting_human_node=awaiting_spec_change_node` |
| `AWAITING_HUMAN` | L3 抽查等待人工确认 | Human | `pre_awaiting_human_node=delivery_review_node` |
| `RETRY` | 准备重试 | Scheduler | `retry_count < max_retry` |
| `DONE` | 完成归档 | Scheduler | 全部产物、Agent 覆盖、Done passed |
| `FAILED` | 预算/超时失败 | Scheduler | 终态 |
| `ESCALATED` | 升级人工处理 | Scheduler/Human | 终态 |

---

## 6. 状态与 GraphFlow 节点关系

同一个状态可能对应不同业务节点，所以当前任务卡额外引入：

```yaml
graph_node: 当前业务节点
pre_awaiting_human_node: 人工暂停前的业务节点
```

| 任务状态 | 可能业务节点 |
|---|---|
| `PENDING` | `spec_binding_node`（等待 Snapshot 绑定） |
| `RUNNING` | `planning_node`、`architecture_impact_node`、`design_spec_node`、`drg_compile_node`、`implementation_drg_node`（含 N×WP）、`conformance_node`、`deploy_node` |
| `AWAITING_AUTO_GATE` | `plan_gate_node`、`architecture_gate_node`、`design_gate_node`、`final_gate_node` |
| `AWAITING_REVIEW` | `delivery_review_node` |
| `AWAITING_SPEC_CHANGE` | 从 `plan_gate_node`（SPEC_MISMATCH）或 `conformance_node`（SPEC_MISMATCH）暂停 |
| `AWAITING_HUMAN` | 从 `delivery_review_node`（L3_REVIEW_REQUIRED）暂停 |
| `APPROVED` | 单个 Agent 通过、最终 Review 通过 |
| `DONE` | `done_node` |

---

## 7. Artifact 周转规则

| 产物 | 产生者 | 读取者 | round |
|---|---|---|---|
| `spec-snapshot.json`（已发布） | SpecAgent（SPEC 层） | spec_binding_node、全部下游 | `round-0` |
| `spec-binding.json` | Gate-Auto | Scheduler、全部下游 | `round-0` |
| `execution-plan.json` | Orchestrator-LLM | plan_gate、drg_compile | `plan_round` |
| `architecture.json` | ArchitectAgent | Gate、design_spec、Dev、Test | `arch_round` |
| `design-spec.json` | ArchitectAgent（二次调用） | design_gate、drg_compile、Dev、Test | `arch_round` |
| `execution-graph.json` | drg_compile_node | implementation_drg | `compile_round` |
| `{wp}_implement` 产物 | DevAgent | Test、Gate、Review | 当前 round |
| `{wp}_test` 产物 | TestAgent | Gate、Review | 当前 round |
| `gate-result.json` | Gate-Auto | Scheduler、Review | 当前 round |
| `conformance.json` | Conformance | Scheduler、delivery_review | 当前 round |
| `review.json` | ReviewAgent | Scheduler | 当前 round |
| `deploy-result.json` | DevOpsAgent | Scheduler | 当前 round |

---

## 8. 硬闸门

| 闸门 | 触发点 | 通过条件 | 失败动作 |
|---|---|---|---|
| Publish Gate | SpecAgent 后（SPEC 层） | `spec-snapshot.json` 15 字段完整 + content_hash 一致 | SpecSnapshot 不发布 |
| Spec Binding Gate | `spec_binding_node` 后 | SpecRef 引用有效 + content_hash 匹配 | Retry |
| Plan Gate | `planning_node` 后 | `execution-plan.json` 完整 + requirement/acceptance 全覆盖 | SPEC_MISMATCH -> awaiting_spec_change；FAIL -> retry |
| Architecture Gate | `architecture_impact_node` 后 | `architecture.json` 完整 | Retry |
| Design Gate | `design_spec_node` 后 | `design-spec.json` 完整（无则 auto-PASS） | Retry |
| DRG Compile Gate | `drg_compile_node` 后 | `execution-graph.json` DAG + 14 治理节点齐全 + content_hash 一致 | Retry |
| Final Gate | `implementation_drg_node` 后 | all_drg_nodes_done | Retry |
| Conformance Gate | `conformance_node` 后 | trace_matrix PASS（MUST 阻塞，SHOULD/MAY 不阻塞） | SPEC_MISMATCH -> awaiting_spec_change |
| Delivery Review（L2） | `delivery_review_node` 后 | Review APPROVE | REJECT -> retry；L3 -> awaiting_human |
| DONE Gate | 归档前 | 产物齐全、Agent 覆盖、Done 全 passed | 拒绝 DONE |

---

## 9. 完整链路（模式 A）

```text
【SPEC 层（DRG 之外）】
用户输入需求
  ↓
SpecAgent（9 skill 固化，opt-in）
  - PM Discussion（create-prd 必选 + 3 可选）
  - Discovery & Assumptions（4 可选）
  - Job Stories（可选）
  ↓
ChangeSpec Draft
  ↓
Publish Gate（scheduler/spec_flow.py:validate_change_spec_payload）
  ↓
SpecSnapshot（不可变，落盘 specs/{project}/published/...）

【GraphFlow 层（DRG 执行）】
spec_binding_node（绑定 Snapshot 版本）
  ↓
planning_node（v1 execution-plan，1:1 拆解 EARS）
  ↓
plan_gate_node
  ↓
architecture_impact_node（architecture.json）
  ↓
architecture_gate_node
  ↓
design_spec_node（design-spec.json）
  ↓
design_gate_node
  ↓
drg_compile_node（P1：rich design spec → refine plan v2 → 重编译 DRG）
  ↓
implementation_drg_node
  - WP1_implement → WP1_test
  - WP2_implement → WP2_test
  - ...（串行，ready[0] only）
  ↓
final_gate_node
  ↓
conformance_node（trace_matrix + 三态）
  ↓
delivery_review_node（L2 语义评审）
  - APPROVE -> deploy
  - REJECT -> retry / escalate
  - L3_REVIEW_REQUIRED -> awaiting_human_node
  ↓
deploy_node
  ↓
done_node（归档）
```

---

## 10. 最终输出

| 输出类别 | 内容 |
|---|---|
| 用户可见交付 | 功能说明、运行方式、测试结果、部署/构建结果 |
| 代码产物 | `{wp}_implement` 产物（diff 或文件） |
| 质量产物 | `{wp}_test` 产物、`gate-result.json`、`conformance.json`、`review.json` |
| 规约产物 | `spec-snapshot.json`（已发布）、`architecture.json`、`design-spec.json`、`execution-plan.json`、`execution-graph.json` |
| 运行轨迹 | `.loop/events/{run_id}.jsonl` |
| 长期记忆 | `.loop/memory/`（artifact-index / failure-memory / recipes / agent memory） |
| 任务状态 | RunState 归档到 `.loop/runs/{run_id}/` |
