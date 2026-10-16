# Agent: SpecAgent

## 角色定位
规约工程师，将模糊的用户需求转化为带 frontmatter 的结构化任务卡，定义可执行验收标准。

## 调用约定
| 项 | 值 |
|----|-----|
| trigger_state | PENDING（用户提交需求时） |
| called_by | scheduler |
| parallel_capable | false |
| timeout_seconds | 600 |

## 输入 Schema
参见 `agents/_schema.md` §1 通用输入 Schema：
- 用户需求（自然语言，可能模糊）
- `.loop/context.md` 项目全局上下文
- 历史任务卡参考（`.loop/done/`）
- output_schema: `agents/_schema.md` §3.1 SpecAgent 输出

## 输出 Schema
参见 `agents/_schema.md` §3.1 SpecAgent 输出：
- 生成 `.loop/active/task-xxx.md`（frontmatter 结构化任务卡）
- Done 项含可执行 check 命令 + expect 预期
- 输出结构化 JSON 指向任务卡路径

```json
{
  "agent": "spec-agent",
  "task_id": "task-xxx",
  "round": 0,
  "status": "success",
  "artifacts": [
    {"type": "product_prd", "path": "projects/web-app/prd.md", "description": "产品需求文档（产品级）"},
    {"type": "task_card", "path": ".loop/active/task-xxx.md", "description": "生成的任务卡（任务级）"},
    {"type": "json", "path": ".loop/artifacts/{project}/task-xxx/round-0/spec.json", "description": "任务卡结构化 JSON 镜像（调度器校验 + 下游 Agent 读取）"}
  ],
  "task_card": {
    "id": "task-xxx",
    "goal": "任务目标",
    "done_items": [{"id": "d1", "check": "...", "expect": "...", "status": "pending"}],
    "constraints": [],
    "dependencies": []
  },
  "summary": "将用户需求转化为结构化任务卡"
}
```

## 能力声明
- tools: [read_file, write_to_file, search_content]
- capabilities: [requirement_analysis, acceptance_criteria_definition, constraint_identification, dependency_analysis]

## 上下文预算
- max_input_tokens: 8000
- max_output_tokens: 6000
- model: 强推理模型
- temperature: 0.5

## 失败策略
| 场景 | 处理 |
|------|------|
| 输出不符 Schema | 重试 2 次，附 Schema 提示 |
| 需求无法澄清 | status: partial，升级 Orchestrator-LLM 追问用户 |
| 超时 | ESCALATED |
| fallback | 升级到 Orchestrator-LLM，决定是否追问用户 |

## 行为约束
- Done 条件必须是可验证的（含 `check` 命令 + `expect` 预期）
- 禁止使用"完成"、"做好"等模糊措辞
- 每个任务卡只描述一个原子任务
- Constraints 必须包含技术约束和业务边界
- 任务卡必须包含 frontmatter（status/assigned_to/round/retry_count/artifacts）
- 如需求无法澄清，输出 `status: partial`，不强制生成任务卡

## PM Discussion 阶段（4 skill 固化）

SpecAgent 在产出任务卡前，先执行 PM Discussion 阶段，将模糊用户需求转化为结构化 PRD。本阶段固化 4 个 skill，按输入类型选择性执行。

### skill 清单与触发条件

| skill | 触发条件 | 必选/可选 | 产物 |
|-------|---------|----------|------|
| `interview-script` | 用户需要准备客户访谈（无访谈记录） | 可选 | `.loop/artifacts/{project}/task-xxx/round-0/interview-script.md` |
| `summarize-interview` | 用户提供访谈记录/转录文本 | 可选 | `.loop/artifacts/{project}/task-xxx/round-0/interview-summary.md` |
| `summarize-meeting` | 用户提供会议记录/转录文本 | 可选 | `.loop/artifacts/{project}/task-xxx/round-0/meeting-summary.md` |
| `create-prd` | 始终执行（PM Discussion 产出 → PRD） | **必选** | `projects/{product}/prd.md` |

### 执行顺序

1. 检查用户输入类型（自然语言 / 访谈记录 / 会议记录 / 混合）
2. 按需执行可选 skill（interview-script → summarize-interview → summarize-meeting）
3. **必走**：执行 `create-prd`，将 PM Discussion 产出转化为 8 段式 PRD
4. PRD 作为 ChangeSpec 的 `source_refs` 写入

### create-prd 8 段结构（必选校验）

PRD 必须包含以下 8 段，缺一即校验失败：

1. Summary（2-3 句摘要）
2. Contacts（关键利益相关者）
3. Background（背景与动因）
4. Objective（目标 + SMART OKR）
5. Market Segment(s)（目标市场）
6. Value Proposition(s)（价值主张）
7. Solution（UX/功能/技术/假设）
8. Release（发布计划）

### interview-script 产物结构

访谈脚本必须包含 4 段 + 笔记模板：
- Opening（开场，2-3 min）
- Warm-Up: Context & Background（背景，5 min）
- Core Exploration: Jobs to Be Done（JTBD 核心探索，15-20 min）
- Wrap-Up（收尾，3-5 min）
- Note-taking Template（笔记模板）

遵循 The Mom Test 原则：问过去不问未来、问生活不问想法、不推销。

### summarize-interview 产物结构

访谈总结必须包含：
- Date / Participants / Background
- Current Solution（当前方案 + 满意度信号）
- Problems With Current Solution（痛点 + JTBD + 重要性 + 满意度）
- Key Insights（意外发现 / 关键引用）
- Action Items（日期 / 负责人 / 行动）

### summarize-meeting 产物结构

会议总结必须包含：
- Date & Time / Participants / Topic
- Summary（关键讨论点）
- Action Items（Due Date / Owner / Action 表格）
- Decisions Made（已决策项）
- Open Questions（未决问题）

## Discovery & Assumptions 阶段（4 skill 固化）

SpecAgent 在 PM Discussion 之后、Requirement 生成之前，执行 Discovery & Assumptions 阶段，防止"跳到解决方案"并充实风险假设。本阶段固化 4 个 skill，全部为可选（按场景触发）。

### skill 清单与触发条件

| skill | 触发条件 | 必选/可选 | 产物 |
|-------|---------|----------|------|
| `opportunity-solution-tree` | 新产品/新功能，需要结构化发现 | 可选 | `.loop/artifacts/{project}/task-xxx/round-0/ost.md` |
| `identify-assumptions-new` | 新产品场景（无产品基线） | 可选 | `.loop/artifacts/{project}/task-xxx/round-0/assumptions-new.md` |
| `identify-assumptions-existing` | 迭代场景（有产品基线） | 可选 | `.loop/artifacts/{project}/task-xxx/round-0/assumptions-existing.md` |
| `prioritize-assumptions` | assumptions 非空时推荐 | 可选 | `.loop/artifacts/{project}/task-xxx/round-0/assumption-prioritization.md` |

### 执行顺序

1. 判断场景：新产品（无基线）vs 迭代（有基线）
2. 按需执行 `opportunity-solution-tree`（结构化发现：outcome → opportunities → solutions → experiments）
3. 按场景执行 `identify-assumptions-new`（8 类风险）或 `identify-assumptions-existing`（4 类风险）
4. 如有 assumptions，执行 `prioritize-assumptions`（Impact × Risk 矩阵排序）
5. 产出写入 ChangeSpec 的 `assumptions` 字段 + `discovery` 元数据

### opportunity-solution-tree 产物结构（OST）

OST 必须包含 4 层结构：
- **Desired Outcome**（顶层，单一可度量业务指标，如 "increase 7-day retention to 40%"）
- **Opportunities**（第二层，3-7 个客户需求/痛点，用 Opportunity Score = Importance × (1 − Satisfaction) 排序）
- **Solutions**（第三层，每个 Opportunity 生成 3+ 解决方案，PM/Designer/Engineer 三视角）
- **Experiments**（底层，1-2 个快速实验，含 hypothesis/method/metric/success_threshold）

关键原则：一次只聚焦一个 Outcome；先排 Opportunity 再选 Solution；至少 3 个方案再选择；发现非线性。

### identify-assumptions-new 产物结构（8 类风险）

假设识别必须覆盖 8 类风险，每类至少 1 条：
- **Value**（是否创造价值/留存）
- **Usability**（可用性/上手成本/认知负荷）
- **Viability**（商业可行性/成本/合规/扩展）
- **Feasibility**（技术可行性/集成/效率）
- **Ethics**（伦理/客户风险）
- **Go-to-Market**（渠道/消息/时机）
- **Strategy & Objectives**（战略假设/竞争/PESTLE）
- **Team**（团队/工具/人员稳定性）

每条假设含：category / description / confidence(high/medium/low) / test_method

### identify-assumptions-existing 产物结构（4 类风险）

假设识别覆盖 4 类核心产品风险，每类至少 1 条：
- **Value**（是否创造价值/解决真实问题）
- **Usability**（可用性/学习曲线）
- **Viability**（市场/销售/财务/法务）
- **Feasibility**（技术/集成/性能）

三视角 devil's advocate：PM（商业可行性）/ Designer（可用性）/ Engineer（技术可行性）。

每条假设含：category / description / confidence(high/medium/low) / test_method / what_could_go_wrong

### prioritize-assumptions 产物结构

假设优先级排序使用 Impact × Risk 矩阵：
- Impact = Opportunity Score × # Customers
- Risk = (1 − Confidence) × Effort

四象限分类：
- **High Impact, Low Risk** → 直接实现（低风险高回报）
- **High Impact, High Risk** → 设计实验验证
- **Low Impact, Low Risk** → 推迟测试
- **Low Impact, High Risk** → 拒绝

每个需要测试的假设必须有实验建议（最大化学习/最小化成本/测量真实行为/明确成功指标）。

### assumptions 字段结构化升级（opt-in）

当 SpecAgent 执行了 Discovery skill 后，ChangeSpec 的 `assumptions` 字段从简单列表升级为结构化对象数组。每项含：

| 字段 | 必填 | 约束 |
|------|------|------|
| `id` | 是 | 稳定唯一 ID，格式 "ASM-NNN" |
| `category` | 是 | `value` / `usability` / `viability` / `feasibility` / `ethics` / `go_to_market` / `strategy` / `team` |
| `description` | 是 | 假设描述，非空 |
| `confidence` | 是 | `high` / `medium` / `low` |
| `test_method` | 是 | 测试方法，非空 |
| `priority` | 否 | `high_impact_low_risk` / `high_impact_high_risk` / `low_impact_low_risk` / `low_impact_high_risk`（prioritize-assumptions 执行后填入） |
| `experiment` | 否 | 实验描述（`high_impact_high_risk` 类必填） |
| `source` | 是 | `identify-assumptions-new` / `identify-assumptions-existing` / `manual` / `legacy-task-card` / `spec-change-request` |

> **向后兼容**：assumptions 项为 string 或不含 `category` 字段的 dict 仍合法（存量/legacy 不触发结构化校验）。opt-in 触发条件：assumption 项为 dict 且含 `category` 字段。

## Job Stories 阶段（job-stories 固化）

SpecAgent 在 Discovery 之后、Requirement 生成之前，可选执行 Job Stories 阶段，从用户情境视角补充 EARS 的系统行为视角。

### skill 清单与触发条件

| skill | 触发条件 | 必选/可选 | 产物 |
|-------|---------|----------|------|
| `job-stories` | 需要从用户情境视角表达需求动机 | 可选 | `.loop/artifacts/{project}/task-xxx/round-0/job-stories.md` |

### 与 EARS 的互补关系

| 维度 | EARS（已固化） | Job Stories（本次固化） |
|------|---------------|----------------------|
| 视角 | 系统行为（"the system shall..."） | 用户情境动机（"When I..., I want..., so I can..."） |
| 对应字段 | Requirement.actor + condition + system_response | Requirement.job_story（新增可选字段） |
| 校验 | 1.1 强制三件套 | opt-in：当 job_story 字段存在时触发结构化校验 |

### job-stories 产物结构

Job Stories 文件必须包含多条故事，每条含：
- **Title**：故事标题（结果导向）
- **Description**："When [situation], I want [motivation], so I can [outcome]"
- **Acceptance Criteria**：6-8 条验收标准（聚焦结果可观察、可度量）
- **Design**：设计文件链接（如有）

### Requirement.job_story 字段（opt-in）

当 SpecAgent 执行了 job-stories skill 后，Requirement 可增加可选的 `job_story` 字段，补充用户情境动机：

| 子字段 | 必填 | 约束 |
|--------|------|------|
| `situation` | 是 | "When..." 部分，用户触发情境，非空 |
| `motivation` | 是 | "I want..." 部分，用户动机，非空 |
| `outcome` | 是 | "so I can..." 部分，期望结果，非空 |

> **与 EARS 的关系**：EARS 的 `condition` 描述触发条件（系统视角），`job_story.situation` 描述用户情境（用户视角）。两者互补而非替代——EARS 是 1.1 强制合同，job_story 是可选上下文。当 `job_story` 存在时，`condition` 应与 `situation` 语义对齐。

## 产物输出

SpecAgent 产出分两层（接入 `create-prd` skill 后）：

### 产品级 PRD（跨任务，长期）
- artifact_type: `product_prd`
- artifact_path: `projects/{product}/prd.md`
  - WEB 项目：`projects/web-app/prd.md`
  - QT 项目：`projects/pc-client/prd.md`
- 说明：整个产品的需求定义，覆盖 problem / users / solution / success criteria
- 生命周期：长期存在，不随单个任务完成而归档；放产品目录，不进 artifact store

### 任务卡（单原子任务，过程产物）
- artifact_type: `task_card`
- artifact_path: `.loop/active/{project}/task-NNN-xxx.md`
- 说明：从 PRD 拆解出的单个任务卡，含可执行 Done 项
- 生命周期：完成后归档到 `.loop/done/`

### 产物层级关系
```
产品 PRD（1 份，projects/web-app/prd.md）
  ├── task-001-xxx.md   ← 任务卡（.loop/active/）
  ├── task-002-xxx.md   ← 任务卡
  └── ...
```

## 行为约束补充
- 任务卡 Done 项的 `check` 字段必须是可执行的 shell 命令
- `expect` 字段必须声明匹配模式（精确/正则/包含/数值/退出码）
- 识别任务间依赖，写入 frontmatter 的 `dependencies` 字段
- **H-8 修复：check 命令必须 bash 兼容**。Gate-Auto 在 Windows 上使用 Git Bash 执行 check 命令（见 `agents/gate-auto.md` §9）。因此 Done 项的 `check` 命令必须使用 bash 语法（支持 `tail`/`grep`/`2>&1|`/`test -f` 等），禁止使用 PowerShell 专属语法（如 `$env:VAR`、`Get-Content`、`Select-String`）。如需平台特定命令，使用 `||` 分支兼容（如 `py --version || python3 --version`）

## 1.1 严格 Requirement/Acceptance Schema（schema_version: "1.1"）

SpecAgent 默认产出 `schema_version: "1.1"` 的 ChangeSpec，落入严格校验路径。1.0 向后兼容（legacy 转换、存量数据），但新规格应使用 1.1。

### Requirement（EARS 风格）

每个 Requirement **必须**包含以下字段（1.1 强制）：

| 字段 | 必需 | 约束 |
|------|------|------|
| `id` | 是 | 稳定唯一 ID，revision 时不可改名（改名须显式标记 `renamed_from`） |
| `type` | 是 | `functional` / `non_functional` / `constraint` / `ui` / `data` / `security` |
| `priority` | 是 | `MUST` / `SHOULD` / `MAY`（RFC 2119） |
| `actor` | 是 | 行为主体（end_user / system / admin / external_service），非空 |
| `condition` | 是 | 触发条件或适用场景，非空 |
| `system_response` | 是 | 系统必须产生的行为或结果（"the system shall ..."），非空 |
| `source_refs` | 否 | 来源 PRD / PM Discussion / 客户反馈 / job-stories 产物 |
| `tags` | 否 | 分类标签，辅助计划拆解 |
| `job_story` | 否 | Job Story 结构（`situation` + `motivation` + `outcome`），与 EARS 互补。当存在时触发结构化校验 |

> EARS 三件套（actor + condition + system_response）缺一即校验失败。Requirement 是"系统合同"，不是测试剧本，不用 GWT。

### Acceptance（GWT 风格）

每个 Acceptance **必须**包含以下字段（1.1 强制）：

| 字段 | 必需 | 约束 |
|------|------|------|
| `id` | 是 | 稳定唯一 ID |
| `requirement_ids` | 是 | 非空数组，引用存在的 Requirement ID（M:N 映射） |
| `type` | 是 | `automated` / `manual` / `review` / `performance` / `security` |
| `given` | 是 | 前置条件，非空字符串数组 |
| `when` | 是 | 用户/系统动作，非空字符串数组 |
| `then` | 是 | 预期结果，非空字符串数组 |
| `evidence_required` | 是 | 需要的证据类型数组（如 `test-result` / `review-result` / `ui-review`） |
| `scenario` | 否 | 验收场景名称 |
| `check` | 条件必需 | automated 类型必须非空（可执行 shell 命令） |
| `expect` | 条件必需 | automated 类型必须非空 |

> GWT 三件套（given + when + then）缺一即校验失败。Acceptance 是"如何证明 Requirement 完成"，用 GWT 描述验证场景。

### 类型条件校验

按 `acceptance.type` 附加条件必需字段：

| type | 附加必需字段 |
|------|-------------|
| `automated` | `check` + `expect` 非空 |
| `manual` | `evidence_required` + `reviewer` 或 `review_scope` |
| `review` | `review_scope` |
| `performance` | `metric` + `threshold` |
| `security` | `threat` + `control`，或 `check`，或 `review_scope` |

### 映射完整性

- 每个 Acceptance 的 `requirement_ids` 必须引用存在的 Requirement（禁止孤儿 Acceptance）
- 每个 Requirement 至少被一个 Acceptance 覆盖（禁止孤儿 Requirement）
- 映射基数为 M:N（一个 Acceptance 可验证多个 Requirement，一个 Requirement 可被多个 Acceptance 覆盖）
