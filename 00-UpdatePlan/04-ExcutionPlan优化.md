# ExcutionPlan 优化路线

## 1. 问题背景

当前系统已经能够从 Published SpecSnapshot 生成 ExecutionPlan，并进一步编译成 DRG。

早期 ExecutionPlan 的生成方式偏机械：

```text
一个 Requirement
  -> 一个 WorkPackage
  -> 一个 implement node
  -> 一个 test node
```

并且存在一个关键问题：

```text
所有 Acceptance Criteria 主要分配给第一个 WorkPackage。
```

这会导致：

- 后续 WorkPackage 没有明确验收目标；
- 测试节点不知道自己真正验证哪个 Requirement；
- Conformance 只能检查 artifact 存在，无法准确说明哪个 Requirement 被哪个 Acceptance 证明；
- 多 Requirement 场景下，计划拆解和证据追踪容易失真。

## 2. 本轮已完成的优化

本轮已完成 ExecutionPlan 机械拆解问题的第一阶段优化。

核心变化：

```text
Acceptance 有 requirement_ids -> 分配到对应 Requirement 的 WorkPackage
Acceptance 没有 requirement_ids -> 兼容旧数据，分配到第一个 WorkPackage
Acceptance 指向不存在的 Requirement -> ExecutionPlan 校验失败
```

也就是说，计划生成从：

```text
所有 Acceptance -> 第一个 WorkPackage
```

升级为：

```text
Acceptance
  -> requirement_ids
  -> 对应 Requirement
  -> 对应 WorkPackage
```

## 3. 当前 ExecutionPlan 输出结构

WorkPackage 当前新增了以下元数据：

```json
{
  "id": "wp-req-ui",
  "title": "Implement REQ-UI: Render settings screen",
  "node_type": "implementation",
  "agent": "frontend-dev",
  "requirements": ["REQ-UI"],
  "acceptance_criteria": ["ACC-UI"],
  "requirement_type": "ui",
  "priority": "MUST",
  "tags": ["ui"],
  "implementation_focus": ["ui surface"],
  "planning_basis": "requirement_acceptance_mapping"
}
```

Acceptance Coverage 当前会记录：

```json
{
  "acceptance_id": "ACC-UI",
  "work_package_id": "wp-req-ui",
  "verification": "test",
  "requirement_ids": ["REQ-UI"],
  "planning_basis": "acceptance_requirement_ids"
}
```

## 4. 已完成校验规则

当前已支持：

```text
ExecutionPlan 必须覆盖 Snapshot 中所有 Requirement
ExecutionPlan 必须覆盖 Snapshot 中所有 Acceptance
Acceptance Coverage 必须指向存在的 WorkPackage
Acceptance.requirement_ids 如果存在，必须能映射到 WorkPackage 中的 Requirement
```

如果 Acceptance 指向不存在的 Requirement，会产生：

```text
execution_plan.invalid_acceptance_requirement_ref
```

## 5. 当前链路效果

示例输入：

```json
{
  "requirements": [
    {"id": "REQ-UI", "type": "ui", "summary": "Render settings screen", "tags": ["ui"]},
    {"id": "REQ-API", "type": "api", "summary": "Persist settings", "tags": ["api"]}
  ],
  "acceptance_criteria": [
    {"id": "ACC-UI", "requirement_ids": ["REQ-UI"], "summary": "Settings screen is visible"},
    {"id": "ACC-API", "requirement_ids": ["REQ-API"], "summary": "Settings are persisted"}
  ]
}
```

计划输出：

```text
REQ-UI -> wp-req-ui -> ACC-UI
REQ-API -> wp-req-api -> ACC-API
```

而不再是：

```text
REQ-UI  -> wp-req-ui  -> ACC-UI + ACC-API
REQ-API -> wp-req-api -> []
```

## 6. 当前仍存在的边界

本轮优化解决的是 Acceptance 分配过于机械的问题，但 ExecutionPlan 还没有成为真正的智能计划层。

当前仍存在：

- 一个 Requirement 默认仍主要对应一个 WorkPackage；
- 还没有根据 Design Spec 拆分为组件、API、数据、迁移、测试、文档等任务；
- 没有把多个 Requirement 合并成一个更自然的 WorkPackage；
- 没有按风险自动生成 Spike / Prototype / Research 任务；
- 没有真实容量估算、依赖排序和并行度优化；
- 没有根据项目类型生成专门的 build/test/deploy work packages；
- 还没有利用 `sprint-plan` skill 进行计划质量增强。

## 7. 后续优化路线

### P0：Requirement / Acceptance 映射拆解

状态：已完成第一版。

能力：

```text
Acceptance 按 requirement_ids 分配
旧 Acceptance 兼容兜底
错误 Requirement 引用会被拒绝
WorkPackage 增加 planning_basis 和 implementation_focus
```

剩余可增强：

```text
在 Spec 层强制 Acceptance.requirement_ids
禁止自动化 Acceptance 缺少 check/expect
Conformance 输出 Requirement / Acceptance / WorkPackage / Node / Evidence Trace Matrix
```

### P1：基于 Design Spec 的计划拆解

目标：

```text
ExecutionPlan 不再只从 Requirement 直接生成，
而是从 Design Spec 中的设计对象生成。
```

链路：

```text
Requirement
  -> Design Spec
  -> component_boundaries / api_contracts / data_model / test_strategy
  -> WorkPackage
```

示例：

```text
REQ-LOGIN
  -> LoginForm
  -> AuthService
  -> POST /api/login
  -> User data model
  -> Login tests
```

生成：

```text
wp-login-ui
wp-login-api
wp-login-data
wp-login-tests
```

建议新增 WorkPackage 类型：

```text
ui
frontend
backend
client
api
data
migration
test
docs
deploy
spike
```

### P2：基于 Delta Spec 的增量计划

目标：

```text
ExecutionPlan 只围绕本次 Delta 的影响范围生成任务。
```

链路：

```text
Current Spec
  + Delta Spec
  -> affected_requirements
  -> affected_acceptance_criteria
  -> affected_design_refs
  -> WorkPackages
```

示例：

```text
ADDED REQ-PHONE-LOGIN
  -> 新增实现任务

MODIFIED REQ-LOGIN
  -> 修改已有登录逻辑任务

REMOVED REQ-OLD-CAPTCHA
  -> 删除旧逻辑 + 回归测试任务
```

### P3：计划质量与风险治理

目标：

```text
ExecutionPlan 不只拆任务，还判断计划是否合理。
```

建议加入：

```text
plan_quality_score
risk_tags
critical_path
parallel_groups
estimated_effort
dependency_reason
rollback_plan
test_strategy_ref
```

可接入 skills：

```text
sprint-plan
prioritize-features
prioritization-frameworks
pre-mortem
strategy-red-team
```

### P4：项目类型专用计划模板

目标：

```text
不同项目类型生成不同 WorkPackage 拆解方式。
```

Web 项目：

```text
wp-ui-design
wp-frontend-components
wp-backend-api
wp-integration-tests
wp-build
```

QT 项目：

```text
wp-window-layout
wp-widget-model
wp-signal-slot
wp-qss-style
wp-ctest
wp-package
```

CLI / Backend 项目：

```text
wp-domain-service
wp-cli-command
wp-config
wp-unit-tests
wp-release
```

### P5：Conformance 反向驱动计划修正

目标：

```text
如果 Conformance 发现证据缺口，
不仅生成 SCR，也能反推 ExecutionPlan 缺少哪些任务。
```

示例：

```text
Requirement 有实现证据，但没有测试证据
  -> 生成 test coverage gap
  -> 建议新增 test WorkPackage

Acceptance 有测试，但没有对应 Requirement 映射
  -> 生成 spec mapping gap
  -> 触发 SpecChangeRequest
```

## 8. 推荐实施顺序

建议顺序：

```text
1. 先强化 Spec 层 Requirement / Acceptance Schema
2. 再升级 Conformance Trace Matrix
3. 接入 Design Spec
4. 让 ExecutionPlan 从 Design Spec 派生
5. 引入 Delta Spec 后做增量计划
6. 最后接入 sprint-plan / pre-mortem 等 skill 做计划质量增强
```

原因：

```text
没有严格 Requirement / Acceptance 映射，计划无法可靠拆解；
没有 Design Spec，计划无法知道组件、API、数据边界；
没有 Delta Spec，计划无法只围绕本次变更范围生成；
没有 Conformance Trace Matrix，计划质量无法被执行结果反向验证。
```

## 9. 最终目标

最终 ExecutionPlan 应从“机械任务列表”升级为：

```text
Spec-aware Plan
Design-aware Plan
Delta-aware Plan
Risk-aware Plan
Project-type-aware Plan
Evidence-aware Plan
```

一句话：

```text
ExecutionPlan 的职责不是简单把 Requirement 数量翻译成任务数量，而是把规格、设计、增量、风险和项目类型翻译成可执行、可验证、可追踪的工作图蓝图。
```
