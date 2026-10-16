# Current Spec / Delta Spec 变更方案

## 1. 问题本质

当前系统已经支持：

```text
ChangeSpec Draft -> Published SpecSnapshot v1
Revision ChangeSpec -> Published SpecSnapshot v2
```

但缺少明确表达：

```text
v2 相对 v1 到底新增、修改、删除了什么
```

的机制。

因此系统现在能看到：

```text
Snapshot v1 是什么
Snapshot v2 是什么
```

但不容易回答：

```text
哪些 Requirement 是新增的？
哪些 Acceptance 被修改了？
哪些约束被删除了？
这个变更会不会破坏已有能力？
多个 ChangeSpec 是否改了同一个对象？
已完成变更如何合并回当前产品规格？
```

这就是缺少 Current Spec + Delta Spec 的核心问题。

## 2. Current Spec 是什么

Current Spec 是某个项目当前已经生效的完整规格真相。

它不是某一次变更，而是产品当前状态。

```text
Current Spec = 这个项目目前所有已发布规格合并后的当前合同
```

示例：

```json
{
  "schema_version": "1.0",
  "project": "web-app",
  "current_version": 3,
  "status": "CURRENT",
  "requirements": [
    {
      "id": "REQ-LOGIN",
      "summary": "用户可以登录"
    },
    {
      "id": "REQ-LOGOUT",
      "summary": "用户可以退出登录"
    }
  ],
  "acceptance_criteria": [
    {
      "id": "ACC-LOGIN-INVALID",
      "requirement_ids": ["REQ-LOGIN"],
      "summary": "错误密码时显示错误提示"
    }
  ],
  "constraints": ["不得明文存储密码"]
}
```

它回答：

```text
这个产品现在应该是什么样？
哪些需求已经生效？
哪些约束长期有效？
```

## 3. Delta Spec 是什么

Delta Spec 是一次变更相对 Current Spec 的增量说明。

它不重复描述全部产品规格，而是明确写：

```text
ADDED 什么
MODIFIED 什么
REMOVED 什么
RENAMED 什么
```

示例：

```json
{
  "schema_version": "1.0",
  "project": "web-app",
  "delta_id": "delta-004",
  "base_current_version": 3,
  "change_type": "feature",
  "requirements": {
    "added": [
      {
        "id": "REQ-PHONE-LOGIN",
        "summary": "用户可以使用手机号登录"
      }
    ],
    "modified": [
      {
        "id": "REQ-LOGIN",
        "changes": {
          "summary": {
            "from": "用户可以登录",
            "to": "用户可以使用账号密码或手机号登录"
          }
        }
      }
    ],
    "removed": []
  },
  "acceptance_criteria": {
    "added": [
      {
        "id": "ACC-PHONE-LOGIN-SUCCESS",
        "requirement_ids": ["REQ-PHONE-LOGIN"],
        "summary": "手机号验证码正确时登录成功"
      }
    ],
    "modified": [],
    "removed": []
  }
}
```

它回答：

```text
这次到底改变了当前规格的哪一部分？
```

## 4. 三者关系

```text
Snapshot 负责执行稳定性
Current Spec 负责项目当前事实
Delta Spec 负责变更语义
```

推荐关系：

```text
Current Spec v3
  + Delta Spec delta-004
  -> Published SpecSnapshot v4
  -> GraphRun
  -> Conformance PASS
  -> Current Spec v4
```

## 5. 建议升级后的链路

当前链路：

```text
PM Discussion
  -> PRD
  -> ChangeSpec Draft
  -> Published SpecSnapshot
  -> GraphRun
```

建议升级为：

```text
PM Discussion
  -> PRD
  -> ChangeSpec Draft
  -> Delta Spec
  -> Apply Delta to Current Spec
  -> Published SpecSnapshot
  -> GraphRun
  -> Conformance PASS
  -> Promote Snapshot to Current Spec
```

修订链路：

```text
SpecChangeRequest
  -> Revision ChangeSpec
  -> Delta Spec
  -> Apply Delta to Current Spec
  -> Snapshot vNext
  -> New GraphRun
```

## 6. 建议新增资产

```text
specs/{project}/current/current-spec.json
specs/{project}/deltas/{delta_id}-delta-spec.json
specs/{project}/deltas/{delta_id}-delta-report.json
```

| 资产 | 作用 |
| --- | --- |
| `current-spec.json` | 当前项目生效规格真相 |
| `delta-spec.json` | 本次变更相对 current 的增量 |
| `delta-report.json` | Delta 校验、冲突检测、影响范围分析 |

## 7. Current Spec 推荐结构

```json
{
  "schema_version": "1.0",
  "project": "web-app",
  "status": "CURRENT",
  "current_version": 3,
  "updated_at": "2026-08-26T10:00:00+08:00",
  "source_snapshot": {
    "spec_id": "task-003",
    "version": 3,
    "content_hash": "sha256:..."
  },
  "requirements": [],
  "acceptance_criteria": [],
  "constraints": [],
  "dependencies": [],
  "product_shape": {},
  "history": [
    {
      "snapshot": "specs/web-app/published/task-003/v3/spec-snapshot.json",
      "delta": "specs/web-app/deltas/delta-003-delta-spec.json"
    }
  ]
}
```

## 8. Delta Spec 推荐结构

```json
{
  "schema_version": "1.0",
  "delta_id": "delta-004",
  "project": "web-app",
  "base_current_version": 3,
  "source_change_spec": "specs/web-app/drafts/task-004-change-spec.json",
  "intent": {
    "summary": "新增手机号登录"
  },
  "requirements": {
    "added": [],
    "modified": [],
    "removed": [],
    "renamed": []
  },
  "acceptance_criteria": {
    "added": [],
    "modified": [],
    "removed": [],
    "renamed": []
  },
  "constraints": {
    "added": [],
    "modified": [],
    "removed": []
  },
  "dependencies": {
    "added": [],
    "modified": [],
    "removed": []
  },
  "impact": {
    "affected_requirements": [],
    "affected_acceptance_criteria": [],
    "breaking_change": false,
    "requires_approval": true
  },
  "open_questions": []
}
```

## 9. Delta 校验规则

Delta 发布前建议校验：

```text
base_current_version 必须等于 current-spec.current_version
added.id 不能已经存在于 Current Spec
modified.id 必须存在于 Current Spec
removed.id 必须存在于 Current Spec
renamed.from 必须存在
renamed.to 不能已存在
removed / renamed 必须要求人工审批
Delta open_questions 必须为空
Acceptance.requirement_ids 必须引用 Delta 应用后的有效 Requirement
```

## 10. 冲突检测规则

多个 Delta 并行存在时，应检测：

```text
两个 Delta 修改同一个 Requirement
一个 Delta 删除 A，另一个 Delta 修改 A
两个 Delta 新增相同 ID
一个 Delta 修改 Requirement，但另一个 Delta 修改它关联的 Acceptance
两个 Delta 修改同一个 constraint
```

结果写入：

```text
specs/{project}/deltas/{delta_id}-delta-report.json
```

示例：

```json
{
  "overall": "CONFLICT",
  "conflicts": [
    {
      "type": "modified_same_requirement",
      "requirement_id": "REQ-LOGIN",
      "deltas": ["delta-004", "delta-005"]
    }
  ]
}
```

## 11. 对 ExecutionPlan 的影响

当前 ExecutionPlan 主要从完整 Snapshot 生成计划。

引入 Delta 后，可以优化为：

```text
ExecutionPlan 优先围绕 Delta.impact 生成
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

这样 Plan 会从“按所有 Requirement 机械拆解”变成“按本次变更影响范围拆解”。

## 12. 对 Conformance 的影响

Conformance 不只判断完整 Snapshot 是否有证据，还可以判断：

```text
本次 Delta 是否被完整实现
本次修改有没有影响旧 Requirement
被删除的 Requirement 是否真的移除
修改的 Acceptance 是否重新验证
未受影响的核心 Requirement 是否回归通过
```

也就是：

```text
Delta Conformance + Snapshot Conformance
```

## 13. 建议实施顺序

### P0：Current Spec 和 Delta Spec 最小闭环

```text
1. 新增 current-spec.json schema
2. 新增 delta-spec.json schema
3. 支持 ChangeSpec -> DeltaSpec
4. 支持 Apply Delta -> Snapshot Payload
5. 发布 Snapshot 时记录 delta_trace
```

### P1：Delta 校验和影响范围

```text
1. added / modified / removed / renamed 校验
2. base_current_version 校验
3. breaking_change 判断
4. requires_approval 判断
5. delta-report.json
```

### P2：冲突治理和执行优化

```text
1. 多 Delta 冲突检测
2. ExecutionPlan 基于 Delta impact 拆解
3. Conformance 增加 Delta 范围检查
4. GraphRun 绑定 Snapshot 时同时绑定 delta_trace
5. DONE 后 Promote Snapshot -> Current Spec
```

## 14. 最终推荐架构

```text
ProductSpec        = 长期产品背景和约束
Current Spec       = 当前已生效完整规格
ChangeSpec         = 本次需求草案
Delta Spec         = 本次相对 Current 的变更语义
Published Snapshot = 本次 GraphRun 绑定的不可变执行合同
SCR                = 执行中发现规格问题后的变更请求
```

一句话：

```text
Current Spec 管“现在是什么”，Delta Spec 管“这次改什么”，Snapshot 管“这次执行按哪个不可变合同跑”。
```
