# Design Spec 优化方案

## 1. 问题本质

当前系统已经具备：

```text
architecture_impact_node
product_shape_node
ui_design_node
```

但这些设计相关节点还没有汇总成一个正式、可审批、可追踪的 Design Spec。

当前链路更接近：

```text
SpecSnapshot
  -> ExecutionPlan
  -> DRG
  -> 若干设计相关节点
```

成熟的 Spec-Driven Development 链路更应该是：

```text
SpecSnapshot
  -> Design Spec
  -> Design Gate
  -> ExecutionPlan
  -> DRG
  -> Implementation
```

因此当前缺失的是 Requirement 和 ExecutionPlan 之间的设计合同层。

## 2. Design Spec 的定位

Design Spec 是 Requirement 和 ExecutionPlan 之间的设计合同。

它回答：

```text
为了实现这些 Requirements，系统准备怎么设计？
哪些模块要改？
API、数据、状态、UI、错误处理、权限、测试策略是什么？
每个 Requirement 被哪些设计决策覆盖？
```

它不是：

- PRD；
- ChangeSpec；
- 代码任务；
- 单纯 UI 视觉稿；
- 普通架构说明文档。

分层关系：

```text
PRD / Requirement = 要做什么
Design Spec       = 准备怎么做
ExecutionPlan     = 拆成哪些工作包
DRG               = 节点如何执行
Implementation    = 真实修改代码
```

## 3. 建议新增资产

建议同时保留可复用设计资产和 run-local 运行引用。

可复用设计资产：

```text
specs/{project}/designs/{spec_id}/v{N}/design-spec.json
specs/{project}/designs/{spec_id}/v{N}/design-review.json
```

当前 Run 引用：

```text
.loop/runs/{run_id}/design-spec.json
.loop/runs/{run_id}/design-review.json
```

推荐语义：

| 资产 | 作用 |
| --- | --- |
| `design-spec.json` | Requirement 到模块、API、数据、UI、测试策略的设计合同 |
| `design-review.json` | 对 Design Spec 的审批或评审结论 |
| run-local `design-spec.json` | 当前 GraphRun 绑定使用的设计规格引用 |
| run-local `design-review.json` | 当前 GraphRun 的设计评审证据 |

## 4. 推荐 Design Spec Schema

```json
{
  "schema_version": "1.0",
  "project": "web-app",
  "spec_id": "task-001",
  "spec_version": 1,
  "design_id": "design-task-001-v1",
  "status": "DRAFT",
  "source_snapshot": {
    "spec_id": "task-001",
    "version": 1,
    "content_hash": "sha256:..."
  },
  "architecture": {
    "summary": "登录功能采用前后端分离设计",
    "affected_modules": ["frontend/auth", "backend/auth"],
    "component_boundaries": [
      {
        "name": "LoginForm",
        "type": "frontend_component",
        "responsibilities": ["输入校验", "提交登录请求", "错误展示"],
        "requirements": ["REQ-LOGIN"]
      }
    ]
  },
  "api_contracts": [
    {
      "name": "Login API",
      "method": "POST",
      "path": "/api/login",
      "request": {"username": "string", "password": "string"},
      "response": {"token": "string"},
      "errors": [{"status": 401, "code": "INVALID_CREDENTIALS"}],
      "requirements": ["REQ-LOGIN"]
    }
  ],
  "data_model": [
    {
      "name": "User",
      "fields": [
        {"name": "id", "type": "string"},
        {"name": "password_hash", "type": "string"}
      ],
      "requirements": ["REQ-LOGIN"]
    }
  ],
  "ui_contract": {
    "source": ".loop/runs/run-001/artifacts/ui_design_node.json",
    "requirements": ["REQ-LOGIN"]
  },
  "state_transitions": [
    {
      "name": "login_submit",
      "from": "idle",
      "to": "submitting",
      "on_error": "error",
      "requirements": ["REQ-LOGIN"]
    }
  ],
  "error_handling": [
    {
      "case": "invalid credentials",
      "behavior": "return 401 and show login failure message",
      "requirements": ["REQ-LOGIN"]
    }
  ],
  "test_strategy": [
    {
      "acceptance_id": "ACC-LOGIN-INVALID",
      "test_level": "component",
      "approach": "simulate invalid credentials and assert error message",
      "evidence_required": ["test-result"]
    }
  ],
  "traceability": [
    {
      "requirement_id": "REQ-LOGIN",
      "design_refs": ["LoginForm", "Login API", "login_submit"],
      "acceptance_ids": ["ACC-LOGIN-INVALID"]
    }
  ],
  "open_questions": [],
  "risks": [],
  "approval": {
    "status": "PENDING",
    "approved_by": null,
    "approved_at": null,
    "content_hash": null
  }
}
```

## 5. 建议新增节点

在当前 DRG runtime 中，第一阶段可以采用：

```text
architecture_impact_node
  -> product_shape_node
  -> ui_design_node
  -> design_spec_node
  -> design_gate_node
  -> drg_compile_node
```

更成熟的目标链路是：

```text
SpecSnapshot
  -> Architecture / Design Analysis
  -> Design Spec
  -> Design Gate
  -> ExecutionPlan
  -> DRG
```

也就是说，未来 `scheduler/plans.py` 应该从 Design Spec 生成 WorkPackage，而不是只从 Requirements 机械生成。

## 6. Design Gate 校验规则

Design Gate 应检查：

```text
Design Spec 必须绑定同一个 SpecSnapshot content_hash
open_questions 必须为空
每个 Requirement 至少有一个 design_refs
每个 Acceptance 至少进入 test_strategy
UI 类 Requirement 必须引用 ui_contract
API 类 Requirement 必须引用 api_contracts
数据类 Requirement 必须引用 data_model
风险项必须有 mitigation
approval.content_hash 必须等于 Design Spec 内容 hash
```

## 7. 对 ExecutionPlan 的影响

当前 ExecutionPlan 是：

```text
Requirement -> WorkPackage
```

优化后应调整为：

```text
Requirement
  -> Design Decision / Component Boundary
  -> WorkPackage
```

例如：

```text
REQ-LOGIN
  -> LoginForm
  -> AuthService
  -> POST /api/login
  -> AuthController
  -> SessionStore
```

ExecutionPlan 可以生成：

```text
wp-login-ui
wp-login-api
wp-login-service
wp-login-tests
```

而不是只有：

```text
wp-req-login
```

这会降低“一个大 Requirement 生成一个粗粒度任务”的问题。

## 8. 对 Conformance 的影响

有 Design Spec 之后，Conformance 不再只是：

```text
Requirement -> Node -> Artifact
```

而是：

```text
Requirement
  -> Design Decision
  -> WorkPackage
  -> Code / Test Artifact
  -> Evidence
```

它应能判断：

```text
需求有没有被设计覆盖？
设计有没有被计划覆盖？
计划有没有被实现覆盖？
验收有没有被测试覆盖？
```

## 9. 建议实施顺序

### P0：最小 Design Spec 闭环

```text
1. 定义 design-spec.json schema
2. 新增 design_spec_node
3. 新增 design_gate_node
4. design_spec_node 汇总 architecture + product_shape + ui_design + SpecSnapshot
5. design_gate_node 校验 open_questions、traceability、content_hash
6. GraphRun / EvidenceManifest 纳入 design-spec.json
```

### P1：让 Plan 从 Design Spec 派生

```text
1. ExecutionPlan 读取 Design Spec
2. WorkPackage 按 component_boundaries / api_contracts / data_model / test_strategy 生成
3. Requirement coverage 从 Design traceability 派生
4. Acceptance coverage 从 test_strategy 派生
```

### P2：Design 审批和语义一致性

```text
1. design-review.json
2. approval.content_hash 绑定
3. SpecSnapshot hash 与 DesignSpec source_snapshot hash 对齐
4. intended-vs-implemented 检查 Design -> Code 偏差
5. SCR 支持 design_gap 类型
```

## 10. 最终目标

完成后，系统链路应从：

```text
SpecSnapshot
  -> ExecutionPlan
  -> DRG
  -> Implementation
```

升级为：

```text
SpecSnapshot
  -> Design Spec
  -> Design Gate
  -> ExecutionPlan
  -> DRG
  -> Implementation
```

一句话总结：

```text
Design Spec 是 Requirement 和 Task 之间缺失的设计合同层，它让计划拆解、实现、测试和 Conformance 都有明确的设计依据。
```
