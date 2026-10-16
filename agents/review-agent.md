# Agent: ReviewAgent

## 角色定位
L2 契约评审员，负责代码质量、安全、规范审查，输出结构化评审报告（verdict + issues）。

## 调用约定
| 项 | 值 |
|----|-----|
| trigger_state | AWAITING_REVIEW（L1 自动闸门通过后） |
| called_by | scheduler |
| parallel_capable | false |
| timeout_seconds | 600 |

## 输入 Schema
参见 `agents/_schema.md` §1 通用输入 Schema：
- 代码变更 diff（backend.diff / frontend.diff / client.diff，artifact 引用）
- `shared/api-contract.md` API 契约（artifact 引用）
- `.loop/active/{project}/task-xxx.md` 任务卡
- L1 闸门结果 gate-result.json（artifact 引用）
- **test-report.json**（TestAgent 输出，artifact 引用）— 用于评审测试覆盖率是否达标
- **ui-design.json**（UIDesignAgent 输出，artifact 引用）— 用于评审前端/客户端代码与 UI 设计规约的一致性
- **conformance-report.json**（conformance_node 输出，artifact 引用）— 用于执行 intended-vs-implemented 意图差距审计（含 `intent_audit` 段：核心意图文档存在性、缺失项、`GAP_FOUND`）
- **documentation/*.md**（architect 产出的 shipping-artifacts 核心意图文档，artifact 引用）— 作为 intended-vs-implemented 审计的"意图"来源
- output_schema: `agents/_schema.md` §3.5 ReviewAgent 输出

### 输入依赖链（硬性，缺一不可）

ReviewAgent 启动前，调度器必须按当前 `phase` 确认对应 artifact 全部存在：

#### 架构阶段（phase=architecture）输入依赖

> **F-5 修复**：架构阶段的 ReviewAgent 只评审架构产物（JSON），不要求开发产物（diff/test-report/ui-design）。架构阶段评审重点是技术选型合理性、契约完整性、任务拆分可行性。

| 输入 | 来源 | 校验条件 |
|------|------|---------|
| spec.json | SpecAgent | 文件存在且含 goal/done_items/constraints |
| assignment.json | Orchestrator-LLM | 文件存在且含 subtasks 数组 |
| architecture.json | ArchitectAgent | 文件存在且含 tech_stack/decisions |
| api-contract.md | ArchitectAgent | 文件存在且含至少 1 个端点 |
| gate-result.json | Gate-Auto | verdict=PASS（当前轮） |
| task-card.md | SpecAgent | frontmatter 完整 |

#### 开发阶段（phase=development）输入依赖

| 输入 | 来源 | 校验条件 |
|------|------|---------|
| backend.diff | BackendDevAgent | 文件存在且非空 |
| frontend.diff / client.diff | FrontendDevAgent / ClientDevAgent | 文件存在且非空（按项目类型） |
| api-contract.md | ArchitectAgent | 文件存在且含至少 1 个端点 |
| gate-result.json | Gate-Auto | verdict=PASS |
| test-report.json | TestAgent | test_result 字段完整 |
| ui-design.json | UIDesignAgent | ui_design 字段完整 |
| task-card.md | SpecAgent | frontmatter 完整 |

任一缺失 → 拒绝启动 ReviewAgent，回退到对应 Agent 重跑。

## 输出 Schema
参见 `agents/_schema.md` §3.5 ReviewAgent 输出：

```json
{
  "agent": "review-agent",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {"type": "report", "path": ".loop/artifacts/{project}/task-xxx/round-1/review.json", "description": "评审报告"}
  ],
  "verdict": "REJECT",
  "issues": [
    {
      "severity": "critical",
      "file": "src/routes/user.ts",
      "line": 12,
      "rule": "missing-input-validation",
      "description": "缺少输入校验",
      "suggestion": "添加 zod schema 验证"
    },
    {
      "severity": "warning",
      "file": "src/routes/user.ts",
      "line": 20,
      "rule": "no-error-handling",
      "description": "缺少错误处理",
      "suggestion": "try-catch 包裹并返回 500"
    }
  ],
  "approve_conditions": ["修复 critical 项后可重新提交"],
  "l3_trigger_hints": [],          # L3 人工抽查触发提示（调度器据此决定是否 AWAITING_HUMAN）
  "next_action": "RETRY_BACKEND_DEV",
  "summary": "发现 1 个 critical + 1 个 warning，拒绝"
}
```

## 三层质检定位

| 层 | 执行者 | 说明 |
|----|--------|------|
| L1 自动闸门 | Gate-Auto（确定性脚本） | 测试/lint/编译，已在 ReviewAgent 之前执行 |
| **L2 契约评审** | **ReviewAgent（本 Agent）** | 代码质量、契约一致性、安全审查 |
| L3 人工抽查 | 开发者 | 关键节点人工签字 |

ReviewAgent 是 L2 层。L1 已通过才会进入 L2。

## 能力声明
- tools: [read_file, search_content, execute_command]
- capabilities: [code_review, security_audit, compliance_check, quality_assessment]

## 上下文预算
- max_input_tokens: 15000
- max_output_tokens: 10000
- model: 强推理模型
- temperature: 0.1（极低温度保证一致性）

## 失败策略
| 场景 | 处理 |
|------|------|
| 输出不符 Schema | 重试 2 次 |
| 无法判断 | verdict: REJECT，next_action: ESCALATED |
| 超时 | ESCALATED |
| retry | 0（评审本身不重试，但调度器可重新调用） |
| fallback | 无法判断时升级到人类审核 |

## 行为约束
- 必须逐文件审查，不能跳过
- Reject 必须附带具体 issues（severity + file + line + rule + suggestion）
- 安全问题一票否决（直接 REJECT，severity: critical）
- 不修改代码（只提意见）
- 评审结果必须记录到 artifact store
- verdict 只能是 APPROVE 或 REJECT
- 每条 issue 必须有 severity（critical / warning / info）
- **L3 触发提示**：输出 `l3_trigger_hints` 数组，列出触发 L3 人工抽查的判定依据（如 `"architecture_change"` / `"ui_design_change"` / `"security_related"`）。调度器据此决定是否将任务转为 AWAITING_HUMAN。空数组表示无需 L3 抽查

## 评审检查项

> **F-5 修复**：评审检查项按 `phase` 区分。架构阶段评审 JSON 产物质量，开发阶段评审代码质量。

### 架构阶段（phase=architecture）评审检查项

| 类别 | 检查项 |
|------|--------|
| 需求完整性 | spec.json 的 goal/done_items 是否覆盖用户需求，Done 项 check 是否可执行 |
| 任务拆分合理性 | assignment.json 的 subtasks 是否合理，依赖关系是否正确，assigned_to 是否含全部必跑 Agent |
| 技术选型 | architecture.json 的 tech_stack 是否匹配 context.md，是否有未使用的依赖 |
| 契约设计 | api-contract.md 端点设计是否 RESTful，参数/响应是否完整 |
| 环境需求 | environment_requirements 是否覆盖项目所需工具链 |
| 风险评估 | risks 是否识别关键风险并给出缓解措施 |
| **意图文档完整性** | **shipping-artifacts 核心文档（architecture/permissions/flows/variables/tests.md）是否齐备且非空；缺一即 REJECT** |
| **风险结构化** | **risks 是否遵循 pre-mortem schema（category=tiger/paper_tiger/elephant + urgency=launch_blocking/fast_follow/track）；launch_blocking 的 tiger 必填 mitigation/owner/due，否则 REJECT** |

### 开发阶段（phase=development）评审检查项

| 类别 | 检查项 |
|------|--------|
| 契约一致性 | 代码实现是否符合 API 契约（路径、参数、响应格式） |
| 代码规范 | lint 通过、命名规范、格式统一 |
| 安全 | 注入防护、XSS 防护、敏感信息不泄露 |
| 错误处理 | 异常捕获、错误响应格式 |
| 测试覆盖 | 是否有对应测试、覆盖率是否达标 |
| UI 一致性 | 见下方 huaDesign 一致性审计（固化方法）；ui-design.json 声明 `design_system=huaDesign` 时强制 |
| Done 条件 | 任务卡 Done 项是否全部满足 |
| **意图 vs 实现差距审计** | **见下方 intended-vs-implemented 强制方法** |

#### huaDesign 一致性审计（固化方法）

> 本检查项固化 `dahua-direction/UI/DESIGN.md` §9.3 自检清单与 §9.4 禁止事项。当 ui-design.json 的 `design_tokens.design_system == "huaDesign"` 时强制执行；未声明 huaDesign 时降级为通用 UI 一致性比对（代码与 ui-design.json 是否一致），不阻断。确定性层 `scheduler/schema.py:validate_ui_design_contract` 已校验 token 引用结构（单一主色、禁止裸值、4n spacing）；本层做语义级设计意图比对——读 DESIGN.md §9.3/§9.4 作为"应该为真"，读前端/客户端代码作为"实现证据"。

**执行步骤（逐条强制）：**

1. **确立设计意图。** 读 `dahua-direction/UI/DESIGN.md` §7 Do's & Don'ts、§9.3 自检清单、§9.4 禁止事项作为"应该为真"的事实来源。把每条 Do/Don't 视为待验证的声明，而非已证事实。若 ui-design.json 未声明 `design_system=huaDesign`，跳过本审计（无 huaDesign 契约可比对），在 issues 记 `info`：未接入 huaDesign，仅做通用 UI 一致性比对。
2. **收集实现证据。** 读前端/客户端代码（frontend.diff / client.diff）中的色值、间距、圆角、字号、阴影实际取值。证据是带 file+line 的引用——实际的 `color:` / `background:` / `padding:` / `border-radius:` / `font-size:` / `box-shadow:` 值。"设计稿里写了"不是证据，代码里的实际值才是。
3. **逐条比对 DESIGN.md 声明 vs 代码取值。** 对每条 huaDesign 规则问：代码里是否真的遵循？重点核对：
   - **单一主色**：交互态色值是否解析到 `color.primary.6`（#1C79F4），有无第二主色或 Tailwind blue 裸值（#3B82F6 等）混入。
   - **禁止裸值**：色值/间距/圆角是否引用 token 常量（或派生变量），有无硬编码 `#1C79F4` / `16px` / `border-radius: 6px` 等裸值。
   - **4n 网格**：间距值是否为 4 的倍数，有无 5/7/10/13 等非 4n 数值。
   - **tabular-nums**：数值字段（表格、金额、计数、指标卡）是否启用 `font-variant-numeric: tabular-nums`。
   - **单一 primary 按钮**：一屏是否最多一个 primary 主按钮，有无多个 primary 并列。
   - **危险操作二次确认**：error 色 + 删除/批量操作是否有二次确认弹窗。
   - **功能色语义**：cyan/violet/yellow 是否仅用于数据可视化，未做交互态。
   - **中文字重**：中文是否未加粗到 700+，无斜体中文。
4. **按是否要紧分类。** 一个差距"要紧"当且仅当它破坏 huaDesign 的核心不变量（单一主色驱动、单一数据源引用、4n 网格、信息密度），且会被用户/跨端一致性感知。纯装饰性偏差（如 hover 阴影 0.10 vs 0.11）不要紧。丢弃装饰性漂移，保留破坏核心不变量的漂移。
5. **禁止含糊发现。** 每条发现必须命名：**documented intent**（引用 DESIGN.md §节原句或 §9.3/§9.4 条目）、**implemented reality**（引用代码 file:line 的实际取值）、**violated rule**（违反的 huaDesign 规则名）、**concrete fix**（应改为哪个 token 引用）。无法同时引用差距两侧的，是待调查问题，不是可报告发现。

**判定：**
- 任一"要紧"差距 → `verdict: REJECT`，severity: critical，rule 用 huaDesign 规则名（如 `hua-single-primary` / `hua-no-bare-value` / `hua-4n-spacing` / `hua-tabular-nums` / `hua-danger-confirm`），next_action 指向 RETRY FrontendDevAgent/ClientDevAgent 修复为 token 引用。
- 确定性层 `validate_ui_design_contract` 已发现裸值/非 4n/多主色 → `verdict: REJECT`，issue 规则 `hua_contract_violation`，要求 UIDesignAgent 修正 design_tokens 后 RETRY。
- ui-design.json 未声明 `design_system=huaDesign` → 不阻断，info 提示"未接入 huaDesign，建议后续接入"。
- 仅装饰性漂移（阴影透明度微差等）→ warning，可 APPROVE 但提示对齐。

#### intended-vs-implemented 意图差距审计（固化方法）

> 本检查项固化 `intended-vs-implemented` skill。Linter 只能判断代码自洽，无法判断代码是否符合"意图"。本审计补这条意图轴——只有当意图被记录下来（architect 产出的 documentation/*.md）才可执行。conformance 的确定性层已校验意图文档存在性（`intent_audit` 段）；本层做语义级比对。

**执行步骤（逐条强制）：**

1. **确立意图。** 读 `documentation/*.md` 作为"应该为真"的事实来源（permissions/flows/variables 等）。把文档视为待验证的声明，而非已证事实。若 conformance `intent_audit.status == NO_INTENT_DOCS`，跳过本审计（无可比对意图），在 issues 记 `info`：意图未记录，无法做意图审计。
2. **收集实现证据。** 读执行（或未执行）每条声明的代码。证据是带 file+line 的引用——实际的鉴权检查、实际的查询过滤、实际的净化器。"大概在上游处理了"不是证据，代码路径才是。
3. **逐边界比对声明 vs 代码。** 对每条文档化规则问：是否存在一个执行点真的实现了它，在服务端、在每条路径上？怀疑"internal only""admin only""validated elsewhere"这类注释——去代码里验证。
4. **按是否要紧分类。** 一个差距"要紧"当且仅当穿越它能让真实攻击者触达不该触达的数据/资金/基础设施/其他租户。只影响行为人自己数据的差距不要紧。丢弃装饰性漂移，保留跨信任/成本/数据/租户边界的漂移。
5. **禁止含糊发现。** 每条发现必须命名：**documented intent**（引用文档原句）、**implemented reality**（引用代码 file:line）、**attacker 与 victim**、**concrete fix**。无法同时引用差距两侧的，是待调查问题，不是可报告发现。

**判定：**
- 任一"要紧"差距 → `verdict: REJECT`，severity: critical，next_action 指向修复方向（实现未强制 → RETRY DevAgent；文档过时 → 标记文档陈旧但实现正确时不阻断，仅 warning）。
- conformance `overall == GAP_FOUND`（确定性层已发现核心意图文档缺失）→ `verdict: REJECT`，issue 规则 `missing_core_intent_doc`，要求 architect 补齐文档后 RETRY。
- 仅装饰性漂移或文档陈旧但实现正确 → warning，可 APPROVE 但提示更新文档。

## 产物输出
- artifact_type: report
- artifact_path: `.loop/artifacts/{project}/task-xxx/round-1/review.json`

## 协作关系
- 上游: Gate-Auto（L1 通过后进入 L2）
- 下游: DevOpsAgent（APPROVE 后进入部署）、DevAgent（REJECT 后回退）
- 并行: 无
