# Requirement / Acceptance 优化路线

## 1. 改动目标

当前 Spec 层已经具备 ChangeSpec、Published SpecSnapshot、GraphRun 绑定、ExecutionPlan、DRG、Conformance 和 EvidenceManifest 等运行时闭环。但 Requirement / Acceptance 仍偏基础，主要校验列表是否存在，尚未形成严格的行为规约和验收映射。

本次优化目标是：

```text
Requirement 明确描述“系统必须做什么”
Acceptance 明确描述“如何证明它完成”
二者必须显式关联
没有可验证方式的 Acceptance 不能静默 PASS
ExecutionPlan / DRG / Conformance 必须基于该映射运转
```

## 2. 核心原则

### 2.1 Requirement 不建议使用 GWT 作为主模型

Requirement 的职责是定义系统合同，也就是系统必须具备什么能力或行为。它应该稳定、简洁、可追踪。

推荐使用 EARS / SHALL 风格：

```text
WHEN 用户提交错误密码
THE SYSTEM SHALL 显示登录失败提示
```

或者结构化表达：

```json
{
  "id": "REQ-001",
  "type": "functional",
  "priority": "MUST",
  "actor": "user",
  "condition": "when submitting invalid login credentials",
  "system_response": "the system shall reject login and show an error message"
}
```

不建议 Requirement 使用 GWT 的原因：

- Requirement 会变成测试剧本，而不是需求合同。
- Requirement 和 Acceptance 的形态会高度重复。
- 同一个 Requirement 可能对应多个验收场景，直接写成 GWT 会降低复用和追踪能力。

### 2.2 Acceptance 推荐使用 GWT

Acceptance 的职责是定义如何验收 Requirement，因此适合使用 Given / When / Then。

示例：

```gherkin
GIVEN 用户位于登录页面
WHEN 用户输入错误密码并提交
THEN 页面显示登录失败提示
```

结构化表达：

```json
{
  "id": "ACC-001",
  "requirement_ids": ["REQ-001"],
  "type": "automated",
  "given": ["user is on the login page"],
  "when": ["user submits invalid credentials"],
  "then": ["login is rejected", "an error message is visible"],
  "check": "npm test -- LoginForm",
  "expect": "pass"
}
```

推荐分工：

| 层级 | 推荐模型 | 作用 |
| --- | --- | --- |
| Requirement | EARS / SHALL | 定义系统必须做什么 |
| Acceptance | GWT | 定义如何验收 |
| Test Scenario | GWT + check/expect | 定义具体测试执行 |
| Conformance | Trace Matrix | 判断实现证据是否覆盖 Spec |

## 3. 推荐 Requirement Schema

每个 Requirement 建议至少包含：

```json
{
  "id": "REQ-001",
  "type": "functional",
  "priority": "MUST",
  "actor": "end_user",
  "condition": "when the user submits login credentials",
  "system_response": "the system shall authenticate the user and enter the main workspace",
  "source_refs": ["projects/web-app/prd.md#login"],
  "tags": ["auth", "ui"]
}
```

字段说明：

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `id` | 是 | 稳定唯一 ID，不应随文本小改动频繁变化 |
| `type` | 是 | `functional` / `non_functional` / `constraint` / `ui` / `data` / `security` |
| `priority` | 是 | `MUST` / `SHOULD` / `MAY` |
| `actor` | 建议 | 行为主体，用户、系统、管理员、外部服务等 |
| `condition` | 建议 | 触发条件或适用场景 |
| `system_response` | 是 | 系统必须产生的行为或结果 |
| `source_refs` | 建议 | 来源 PRD、PM Discussion、客户反馈或旧任务卡 |
| `tags` | 建议 | 分类标签，辅助计划拆解和审查 |

## 4. 推荐 Acceptance Schema

每个 Acceptance 建议至少包含：

```json
{
  "id": "ACC-001",
  "requirement_ids": ["REQ-001"],
  "type": "automated",
  "scenario": "invalid password shows error message",
  "given": ["user is on login page"],
  "when": ["user enters wrong password", "user submits the form"],
  "then": ["the system shows an authentication error"],
  "check": "npm test -- LoginForm",
  "expect": "pass",
  "evidence_required": ["test-result"]
}
```

字段说明：

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `id` | 是 | 稳定唯一 ID |
| `requirement_ids` | 是 | 必须关联至少一个存在的 Requirement |
| `type` | 是 | `automated` / `manual` / `review` / `performance` / `security` |
| `scenario` | 建议 | 验收场景名称 |
| `given` | 建议 | 前置条件 |
| `when` | 建议 | 用户或系统动作 |
| `then` | 是 | 预期结果 |
| `check` | 条件必需 | 自动化验收命令 |
| `expect` | 条件必需 | 自动化验收期望输出 |
| `evidence_required` | 是 | 需要的证据类型，例如 `test-result`、`review-result`、`ui-review` |

## 5. 发布前校验规则

ChangeSpec 发布为 Published SpecSnapshot 前，应增加以下校验：

```text
每个 Requirement 必须有唯一 ID
每个 Acceptance 必须有唯一 ID
每个 Acceptance.requirement_ids 必须非空
Acceptance.requirement_ids 必须引用存在的 Requirement
每个 Requirement 至少被一个 Acceptance 覆盖
automated Acceptance 必须有 check + expect
manual Acceptance 必须有 evidence_required + reviewer 或 review_scope
performance Acceptance 必须有 metric + threshold
security Acceptance 必须有 threat/control/check 或 review_scope
```

这样可以避免：

- Requirement 写了但没有验收。
- Acceptance 写了但不知道证明哪个需求。
- 没有 check 的自动化验收被误判通过。
- Conformance 只能检查 artifact 存在，无法判断证据对应哪个需求。

## 6. 对 ExecutionPlan / DRG 的影响

当前 ExecutionPlan 偏机械拆解：

```text
一个 Requirement
  -> 一个 WorkPackage
  -> 一个 Implement Node
  -> 一个 Test Node
```

且 Acceptance Criteria 主要分配给第一个 WorkPackage。

优化后应调整为：

```text
Requirement
  -> 找到关联的 Acceptance
  -> 生成 WorkPackage
  -> implement node 绑定 Requirement
  -> test node 绑定该 Requirement 对应的 Acceptance
```

示例：

```json
{
  "work_package_id": "wp-req-001",
  "requirements": ["REQ-001"],
  "acceptance_criteria": ["ACC-001", "ACC-002"]
}
```

这样 Conformance 才能沿着明确链路判断：

```text
REQ-001
  -> wp-req-001_implement
  -> wp-req-001_test
  -> ACC-001 / ACC-002
  -> test-result artifact
  -> conformance PASS / UNVERIFIED
```

## 7. 对 Conformance 的影响

当前 Conformance 主要检查：

```text
Requirement 是否有 implementation node evidence
Acceptance 是否有 test node evidence
```

优化后 Conformance 应升级为 Trace Matrix：

```text
Requirement
  -> Acceptance
  -> WorkPackage
  -> DRG Node
  -> Artifact
  -> Evidence
```

ConformanceReport 应能明确输出：

```json
{
  "requirement_id": "REQ-001",
  "acceptance_ids": ["ACC-001", "ACC-002"],
  "implementation_nodes": ["wp-req-001_implement"],
  "test_nodes": ["wp-req-001_test"],
  "evidence_refs": [
    ".loop/runs/run-001/artifacts/wp-req-001_implement.json",
    ".loop/runs/run-001/artifacts/wp-req-001_test.json"
  ],
  "status": "PASS"
}
```

## 8. 建议实施顺序

### P0-1：定义严格 Schema

在 `scheduler/spec_flow.py` 中增强 `validate_change_spec_payload`：

- Requirement ID 唯一性校验。
- Acceptance ID 唯一性校验。
- Requirement 必需字段校验。
- Acceptance 必需字段校验。

### P0-2：建立 Requirement / Acceptance 映射

强制：

```text
Acceptance.requirement_ids 非空
Acceptance.requirement_ids 必须引用存在的 Requirement
每个 Requirement 至少被一个 Acceptance 覆盖
```

### P0-3：禁止空验收自动 PASS

按 Acceptance 类型增加条件校验：

```text
automated -> check + expect
manual -> evidence_required + reviewer/review_scope
review -> review_scope
performance -> metric + threshold
security -> threat/control/check 或 review_scope
```

### P0-4：调整 ExecutionPlan 分配逻辑（已完成第一版）

在 `scheduler/plans.py` 中不再把所有 Acceptance 都塞给第一个 WorkPackage，而是按 `acceptance.requirement_ids` 分配。

当前已实现：

```text
Acceptance 有 requirement_ids -> 分配到对应 Requirement 的 WorkPackage
Acceptance 没有 requirement_ids -> 兼容旧数据，分配到第一个 WorkPackage
Acceptance 指向不存在的 Requirement -> ExecutionPlan 校验失败
WorkPackage 增加 requirement_type / priority / tags / implementation_focus / planning_basis
```

### P0-5：升级 ConformanceReport

在 `scheduler/conformance.py` 中输出 Requirement / Acceptance / WorkPackage / Node / Evidence 的追踪矩阵。

## 9. 预期收益

完成后，Spec 层会从“有需求列表和验收列表”升级为：

```text
行为规约明确
验收场景清晰
需求和验收可追踪
执行计划按映射生成
测试节点知道自己验证什么
Conformance 能解释为什么 PASS
EvidenceManifest 能证明交付闭环
```

一句话总结：

```text
Requirement 是合同，Acceptance 是验收，二者的显式映射是 SpecGraphflow 继续走向成熟 SDD 的基础。
```
