# Agent 定义规范（v2.0 工具化）

> 所有 Agent 遵循统一的结构定义，作为**可调用能力**被调度器按需实例化。
> v2.0 变更：Agent 从"角色定义"升级为"工具化能力"，每个 Agent 有固定 IO Schema、上下文预算、失败策略。

---

## 1. 核心理念

**Agent = 可调用能力，不是常驻角色。**

- 调度器按需新开 LLM 会话，注入 Agent 定义 + 上下文包
- Agent 执行完返回结构化 JSON，会话即销毁
- LLM 无状态，所有重要信息落盘到 artifact store
- 交接传引用不传内容

---

## 2. Agent 定义模板（v2.0）

```markdown
# Agent: [名称]

## 角色定位
[一句话描述]

## 调用约定
- trigger_state: [触发此 Agent 的任务状态]
- called_by: scheduler
- parallel_capable: [true|false]
- timeout_seconds: [超时]

## 输入 Schema
[接收什么，引用 agents/_schema.md 的输入模板]

## 输出 Schema
[产出什么，引用 agents/_schema.md 的输出模板或自定义]

## 能力声明
- tools: [可用工具列表]
- capabilities: [能力列表]

## 上下文预算
- max_input_tokens: [输入上限]
- max_output_tokens: [输出上限]
- model: [推荐模型]
- temperature: [推荐温度]

## 失败策略
- retry: [重试次数]
- on_schema_violation: [输出不符 Schema 时的处理]
- on_timeout: [超时处理]
- fallback: [备选方案]

## 行为约束
- [必须遵守的规则]

## 产物输出
- artifact_type: [json|diff|log|report]
- artifact_path: .loop/artifacts/{task_id}/{round}/{name}.{ext}
```

---

## 3. Agent 清单

| Agent | 文件 | 优先级 | 触发状态 | 产物类型 | 状态 |
|-------|------|--------|---------|---------|------|
| Orchestrator-LLM | `orchestrator.md` | P0 | PENDING | assignment.json | ✅ 已重写为调度器设计 |
| SpecAgent | `spec-agent.md` | P0 | PENDING | 任务卡 .md + PRD + PM Discussion 产物 + Discovery 产物 + Job Stories | ✅ 已完成（9 skill 固化） |
| ArchitectAgent | `architect-agent.md` | P1 | DISPATCHED | 架构决策 JSON | ✅ 已完成 |
| BackendDevAgent | `backend-dev.md` | P1 | DISPATCHED | 代码 diff | ✅ 已完成 |
| FrontendDevAgent | `frontend-dev.md` | P1 | DISPATCHED | 代码 diff | ✅ 已完成 |
| UIDesignAgent | `ui-design-agent.md` | P1 | DISPATCHED | UI 设计规约 JSON | ✅ 已完成 |
| ClientDevAgent | `client-dev.md` | P1 | DISPATCHED | 代码 diff（QT 客户端） | ✅ 已完成 |
| TestAgent | `test-agent.md` | P2 | DISPATCHED | 测试报告 JSON | ✅ 已完成 |
| ReviewAgent | `review-agent.md` | P2 | AWAITING_REVIEW | 评审报告 JSON | ✅ 已完成 |
| DevOpsAgent | `devops-agent.md` | P3 | PENDING | 部署结果 JSON | ✅ 已完成 |
| Gate-Auto | `gate-auto.md` | - | AWAITING_AUTO_GATE | 闸门结果 JSON | ✅ 已完成 |

---

## 4. 通用调用接口

调度器通过统一接口调用所有 Agent（见 `orchestrator.md` §8.1）：

```
invoke_agent(agent_name, context_refs) → structured_output
```

- `agent_name`：对应 `agents/{name}.md`
- `context_refs`：artifact 路径列表（不传内容）
- 返回：符合 `agents/_schema.md` 输出 Schema 的结构化 JSON

---

## 5. 产物交接规则

1. 每个 Agent 输出必须是**结构化 JSON**（代码类 Agent 输出 diff）
2. 产物存入 `.loop/artifacts/{task_id}/{round}/{agent_name}.{ext}`
3. 下游 Agent 接收的是** artifact 路径**，按需读取
4. 交接 Schema 详见 `agents/_schema.md`

---

## 6. 失败处理统一规则（硬性强制）

| 失败类型 | 重试上限 | 回滚 | 耗尽动作 |
|---------|---------|------|---------|
| 输出不符 Schema | 2 | 不回滚 | ESCALATED |
| Agent 超时 | 2 | 不回滚 | ESCALATED |
| L1 闸门失败 | max_retry(3) | 回滚 checkpoint | ESCALATED |
| L2 评审拒绝 | max_retry(3) | 回滚 checkpoint | ESCALATED |
| **产物完整性校验失败**（新增） | max_retry(3) | 回滚 checkpoint | ESCALATED |
| **Agent 覆盖率校验失败**（新增） | max_retry(3) | 回滚 checkpoint | ESCALATED |
| **Agent 被跳过**（新增） | max_retry(3) | 回滚 checkpoint，强制回退到缺失 Agent 重跑 | ESCALATED |

### 6.1 硬性强制原则

- **任一校验失败，禁止状态转换**（见 `.loop/state-machine.md` §5 `transition_to()`）
- **不允许选择性执行**：所有必跑 Agent 必须被调度，不可跳过
- **不满足条件不接受下一阶段工作**：产物缺失 / Agent 跳过 / Done 项未 passed → 拒绝推进
- **Layer 0 人工模式同样适用**：AI 充当调度器时必须对照 `.loop/checklists/layer-0-must-run.md` 逐项确认
- 完整校验规则见 `agents/_schema.md` §4 + `.loop/loop.md` §2.3
