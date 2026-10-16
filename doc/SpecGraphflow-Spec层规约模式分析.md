# SpecGraphflow Spec 层规约模式分析

## 1. 结论摘要

当前 SpecGraphflow 的 Spec 层并不是简单的需求文档层，而是一套规格资产生命周期：

```text
需求讨论
→ 需求规格化
→ 发布不可变合同
→ GraphRun 绑定
→ 执行与符合性检查
→ 发现规格偏差
→ 发起规格修订
→ 发布新版本并创建新 Run
```

它已经将 Spec 建设成"不可变、可绑定、可追踪、可修订的执行合同"，并通过 GraphFlow 将规格映射为执行计划、执行图、节点产物和交付证据。

与主流 Spec-Driven Development（SDD）相比，它在规格版本化、运行绑定、证据闭环和规格变更治理方面较强；但在需求表达精度、Design Spec、增量规格、语义一致性分析、项目级治理原则和多变更协同方面仍不完整。

---

## 2. 当前系统的 Spec 规约模式

### 2.1 核心对象关系

```text
PM Discussion
      ↓
Product PRD
      ↓
ChangeSpec Draft ───────┐
      ↓                 │ 合并长期产品约束
Publish Gate        ProductSpec
      ↓
Published SpecSnapshot
      ↓
SpecBinding
      ↓
GraphRun / ExecutionPlan / DRG
      ↓
Conformance
      ↓
SpecChangeRequest
      ↓
Revision ChangeSpec
      ↓
Snapshot v2 → New GraphRun
```

### 2.2 ProductSpec：产品级长期事实

ProductSpec 用于表达跨多个需求长期成立的内容：

- 产品类型；
- 目标用户；
- 核心工作流；
- 产品与技术约束；
- 领域术语。

它相当于单次 ChangeSpec 的全局背景和产品底座。目前结构仍较轻，更接近全局上下文配置，还不是完整的领域规格模型。

### 2.3 PM Discussion 与 PRD：需求澄清层

**已固化**（2026-09-01 升级）：4 个 PM Discussion skill 已固化到 SpecAgent，从"推荐"升级为"固化"。

固化入口：

```text
用户需求
→ PM Discussion（SpecAgent 内执行）
  → interview-script（可选，准备客户访谈）
  → summarize-interview（可选，总结访谈记录）
  → summarize-meeting（可选，总结会议记录）
  → create-prd（必选，8 段式 PRD 生成）
→ Product PRD（projects/{product}/prd.md）
→ ChangeSpec（source_refs 引用 PRD）
```

系统要求：

```text
pm_discussion.skills_executed 含 "create-prd"
且
pm_discussion.prd_sections_present 含全部 8 段
且
source_refs 引用 prd.md
且
open_questions = []
```

满足后才允许生成 PRD 和 ChangeSpec。

固化后，SpecAgent 在产出任务卡前先执行 PM Discussion 阶段，将模糊用户需求转化为结构化 PRD。create-prd 为必选 skill（每次执行都产出 PRD），其余 3 个为可选 skill（按输入类型触发）。scheduler 层（`spec_flow.py:validate_change_spec_payload`）采用 **opt-in 哲学**校验：仅当 ChangeSpec 包含 `pm_discussion` 字段时，才校验 `source_refs` 引用 PRD。存量 ChangeSpec 无 `pm_discussion` 字段，不触发新校验（与 1.1 Strict Spec、huaDesign、Intent audit 同一 opt-in 哲学）。

> 历史不足（已修复）：此前系统只能校验 `status=applied` 字段，不能证明对应 PM Skill 确实执行过。固化后通过 `pm_discussion.skills_executed` + `prd_sections_present` + `source_refs` 三重校验确保 PM Discussion 确实执行。

### 2.4 ChangeSpec：一次变更的合同草案

ChangeSpec 表达一次具体变化，主要包含：

- `intent`：为什么做；
- `scope`：做什么、不做什么；
- `requirements`：系统必须具备的行为；
- `acceptance_criteria`：如何证明完成；
- `constraints`：必须遵守的约束；
- `dependencies`：外部依赖；
- `assumptions`：当前假设；
- `open_questions`：未决问题；
- `product_shape`：产品形态；
- `source_refs`：需求来源。

发布前至少检查：

- 必需字段完整；
- Requirements 非空；
- Acceptance Criteria 非空；
- Open Questions 已清空；
- 修订版具备审批结果。

### 2.5 Published SpecSnapshot：不可变执行合同

ChangeSpec 通过发布门禁后生成：

```text
specs/{project}/published/{spec_id}/v{N}/spec-snapshot.json
```

Snapshot 具有以下语义：

- 只有 `status=PUBLISHED` 的规格才能被 GraphFlow 绑定；
- 每个 Snapshot 带有 `content_hash`；
- 相同 `spec_id + version` 不允许覆盖；
- 草稿可以修改，已发布 Snapshot 不允许原地修改；
- GraphFlow 消费确定版本，不直接消费自然语言需求或草稿。

这是当前 Spec 层最完整、最重要的能力。

### 2.6 SpecBinding：把规格版本变成运行事实

GraphRun 创建时记录：

```json
{
  "bound_spec": {
    "spec_id": "...",
    "version": 1,
    "content_hash": "sha256:...",
    "snapshot_uri": "..."
  }
}
```

由此形成以下规则：

- 一个 Run 固定绑定一个 Snapshot；
- 运行中不能静默切换需求版本；
- Plan、DRG、验收和证据都应追溯到同一个 Snapshot；
- 需求发生实质变化时，应发布新 Snapshot 并创建新 Run。

### 2.7 SpecChangeRequest：执行层向规格层反馈

如果执行期间发现需求缺失、规格冲突或证据无法闭环，GraphFlow 不直接修改原 Snapshot，而是：

```text
Conformance UNVERIFIED
→ SpecChangeRequest
→ AWAITING_SPEC_CHANGE
→ Revision ChangeSpec
→ 人工审批
→ Snapshot v2
→ New GraphRun
→ 旧 Run SUPERSEDED
```

这实现了 Spec 层和执行层之间的受控反馈协议，并保留旧 Run 的历史真实性。

---

## 3. 主流 Spec-Driven Development 的共同思想

目前主流 SDD 方案虽然具体格式不同，但通常包含以下共同原则：

1. **Spec 先于代码**：先明确行为和边界，再决定技术实现。
2. **What 与 How 分离**：Requirements 描述做什么，Design 描述怎么做。
3. **结构化澄清**：在规划前解决歧义、冲突、边界和异常场景。
4. **需求可测试**：使用 EARS、Given/When/Then 或明确 Acceptance Criteria。
5. **Spec 派生 Design、Plan 和 Tasks**：下游产物不能脱离上游规格。
6. **全链路可追踪**：Requirement 能追踪到 Design、Task、Code、Test 和 Evidence。
7. **规格持续演进**：功能变更以 Delta 形式进入当前规格真相。
8. **跨功能治理原则**：通过 Constitution 或 Steering 约束安全、架构、测试和质量。
9. **一致性与漂移检查**：检查 Spec、Design、Tasks、Code 之间的遗漏和冲突。
10. **人机共同审批**：关键阶段可以审查、修改和重新生成，而非直接进入编码。

典型模式包括：

```text
GitHub Spec Kit：
Constitution → Specify → Clarify → Plan → Tasks → Analyze → Implement

Kiro：
Requirements → Design → Tasks → Implementation

OpenSpec：
Current Specs + Change Proposal + Delta Specs
→ Design → Tasks → Implement → Archive/Merge
```

---

## 4. 主流 SDD 思想在本系统中的体现

| 主流思想 | 当前系统体现 | 完成度 |
|---|---|---:|
| Spec 先于代码 | GraphFlow 只能绑定 Published Snapshot | 强 |
| What 与 How 分离 | Spec 描述需求，GraphFlow/Handler 负责执行 | 强 |
| Spec 是执行合同 | Plan、DRG、验收和证据引用 Snapshot | 强 |
| 需求先澄清 | PM Discussion、Open Questions 门禁 | 中 |
| 规格可版本化 | `spec_id + version + content_hash` | 强 |
| 已发布规格不可覆盖 | 已存在的 Snapshot 版本拒绝覆盖 | 强 |
| 需求可追踪 | Requirement → WorkPackage → Artifact | 中 |
| 验收可执行 | Acceptance 支持 `check/expect` | 中 |
| 规格变化受控 | SCR → Revision → Approval → New Run | 强 |
| 规格与实现检查 | ConformanceReport | 中偏弱 |
| 计划由规格派生 | ExecutionPlan 从 Requirement 生成 | 中偏弱 |
| 证据驱动完成 | Artifact、EvidenceManifest、Hash | 强 |
| 人工审批 | Revision 发布审批 | 中 |
| 兼容存量输入 | 旧 TaskCard 可转换为 ChangeSpec | 中 |

与常见 Spec 工具相比，本系统较突出的特点是：

> 它不只生成 Spec、Plan 和 Tasks，还将 Published SpecSnapshot 绑定到真正的 GraphRun，并建立状态、重试、规格修订和 Evidence 闭环。

因此它更接近"Spec 驱动的软件交付运行时"，而不只是"AI 编码前的结构化规划工具"。

---

## 5. 当前缺失或不足的能力

### 5.1 行为规约表达不够严格

当前 Requirement 和 Acceptance 主要只校验列表是否存在，没有强制：

- 唯一、稳定的 Requirement ID；
- 行为主体；
- 触发条件；
- 系统预期行为；
- 正常、异常和边界场景；
- Requirement 与 Acceptance 的明确关联；
- Requirement 之间的冲突检查。

建议引入 EARS 或 Given/When/Then，例如：

```text
WHEN 用户提交无效邮箱
THE SYSTEM SHALL 在邮箱字段旁显示格式错误
```

或者：

```gherkin
GIVEN 用户位于注册页面
WHEN 用户提交格式错误的邮箱
THEN 系统拒绝注册
AND 在邮箱字段旁显示错误原因
```

> **已固化**（2026-09-01）：Requirement 已采用 EARS 风格（`actor` + `condition` + `system_response`），Acceptance 已采用 GWT 风格（`given` + `when` + `then`），1.1 schema_version 强制校验三件套 + M:N 映射 + 类型条件。此外，`job-stories` skill 已固化为可选补充：Requirement 可增加 `job_story` 字段（`situation` + `motivation` + `outcome`），从用户情境视角补充 EARS 的系统行为视角。opt-in：仅当 `job_story` 字段存在时触发结构化校验（`scheduler/spec_flow.py:validate_change_spec_payload`）。但 Requirement 之间的冲突检查尚未实现。

### 5.2 缺少 Current Spec + Delta Spec

当前主要通过完整 Snapshot v1、v2 表达演进，没有显式记录：

```text
ADDED Requirements
MODIFIED Requirements
REMOVED Requirements
RENAMED Requirements
```

因此较难判断：

- 新版本具体改变了什么；
- Requirement 是新增、修改还是删除；
- 删除是否造成兼容性破坏；
- 两个并行 ChangeSpec 是否冲突；
- 已完成变更如何合并回当前产品规格。

### 5.3 缺少 Constitution / Steering

系统尚未把跨功能长期原则建模为强约束，例如：

- 安全基线；
- API 兼容策略；
- 测试覆盖要求；
- 性能预算；
- 数据隐私要求；
- 架构分层；
- UI 一致性；
- 禁用技术和依赖策略。

ProductSpec 的 Constraints 可以承载一部分内容，但缺少 MUST/SHOULD/MAY、优先级、例外审批以及逐阶段符合性验证。

> **部分固化**（2026-09-01）：ChangeSpec 的 `assumptions` 字段已通过 `identify-assumptions-new`（8 类风险）和 `identify-assumptions-existing`（4 类风险）固化为结构化风险假设。每项含 `category`/`confidence`/`test_method`/`priority`/`experiment`，配合 `prioritize-assumptions` 的 Impact × Risk 矩阵排序。opt-in：仅当 assumption 项含 `category` 字段时触发结构化校验（`scheduler/spec_flow.py:validate_change_spec_payload`）。但跨功能长期原则（安全基线/API 兼容策略等）仍需 ProductSpec Constraints 承载。

### 5.4 缺少正式 Design Spec

主流 Requirements-First 流程一般是：

```text
Requirements → Design → Tasks → Implementation
```

当前系统是：

```text
SpecSnapshot → ExecutionPlan → DRG
```

架构影响节点位于 ExecutionPlan 之后，导致 Work Package 不是基于完整架构设计、组件边界、数据模型和接口契约生成的。

建议增加正式、可审批的 Design Spec，并调整为：

```text
SpecSnapshot
→ Architecture/Design Analysis
→ Design Spec
→ Design Gate
→ ExecutionPlan
→ DRG
```

### 5.5 ExecutionPlan 仍是机械拆解

当前基本采用：

```text
一个 Requirement
→ 一个 Work Package
→ 一个 Implement Node
→ 一个 Test Node
```

且 Acceptance Criteria 主要分配给第一个 Work Package。

缺少：

- 按用户故事、组件或领域拆分；
- 一个 Requirement 对应多个任务；
- 多个 Requirement 合并到同一任务；
- 前端、后端、数据库、迁移、文档和部署任务类型；
- 风险验证和技术 Spike；
- 基于依赖关系的真实并行计划。

> **部分固化**（2026-09-01）：`opportunity-solution-tree` 已固化为 SpecAgent 的可选 Discovery skill，在 Requirement 生成前先做结构化发现（outcome → opportunities → solutions → experiments），防止"跳到解决方案"。Requirement 的 `source_refs` 可引用 OST 产物。但 ExecutionPlan 的 WP 拆解仍需 Design Spec P1 的 `refine_plan_from_design_spec` 进一步改善（按 component_boundaries/api_contracts/data_model 派生 WP 而非 1:1 拆解）。

### 5.6 缺少跨产物语义一致性分析

当前主要检查 ID 是否被节点覆盖，但不会深入判断：

- PRD 与 ChangeSpec 是否一致；
- ProductSpec 与 ChangeSpec 是否冲突；
- Design 是否覆盖所有 Requirement；
- Plan 是否忠实于 Design；
- Acceptance 是否真正验证 Requirement；
- 非功能约束是否进入任务和测试；
- 实现是否偏离 Spec 的真实意图。

因此当前是"结构覆盖检查"，还不是"语义一致性分析"。

### 5.7 Conformance 偏向证据存在性

当前 Requirement PASS 主要依赖：

```text
对应 implementation node 已完成
+
对应 Artifact 文件存在
```

更完整的符合性链应该是：

```text
Requirement
→ Design Decision
→ Work Package
→ Code Diff / Commit
→ Test Scenario
→ Test Result
→ Review Result
→ Evidence
```

目前 `intended-vs-implemented` 只是 Skill Contract 声明，尚未成为真实的语义审查执行步骤。

### 5.8 Acceptance Contract 不够严格和安全

当前 Acceptance 可直接包含 shell `check`，存在以下问题：

- 没有 `check/expect` 的条目可能被忽略；
- 空检查集合可能自动判定 PASS；
- shell 尚未运行在真实沙箱中；
- 没有标准化测试环境、前置数据和清理步骤；
- 没有区分单元、集成、E2E、性能、安全和人工验收；
- 没有强制 Requirement 与 Acceptance 的关联。

### 5.9 初次发布审批不足

Revision ChangeSpec 要求审批，但初次 Snapshot 发布主要依赖结构校验，缺少：

- Product Owner 审批；
- Architect 审批；
- Security/Compliance 审批；
- 审批时间；
- 审批对象的 content hash；
- 审批后内容变更自动使审批失效。

### 5.10 缺少并行变更和冲突治理

系统目前没有完整处理：

- 多个 ChangeSpec 同时修改同一 Requirement；
- Delta 冲突检测；
- Spec 分支与合并；
- Requirement 所有权；
- 跨 Spec 依赖图；
- 跨项目、跨仓库规格协调。

版本化解决了历史追踪，但还没有解决多人、多 Agent 并行演进问题。

---

## 6. 能力成熟度判断

### 6.1 已经比较成熟

- Published SpecSnapshot；
- 不可覆盖发布；
- Content Hash；
- Run 固定绑定版本；
- SpecChangeRequest；
- Revision Approval；
- 新版本创建新 Run；
- Requirement/Acceptance 的基础追踪；
- Artifact 与 EvidenceManifest 闭环。

### 6.2 已有框架，但需要增强

- PM 需求澄清；
- ProductSpec；
- Acceptance Contract；
- ExecutionPlan；
- Conformance；
- 人工审批；
- Skill 方法约束。

### 6.3 明显缺失

- EARS 或 Given/When/Then 行为规约；
- Current Spec + Delta Spec；
- Project Constitution / Steering；
- 正式 Design Spec；
- 跨产物语义一致性分析；
- Requirement–Acceptance 多对多追踪；
- 真实代码语义 Conformance；
- 非功能需求结构化模型；
- 并行变更与规格冲突处理；
- 审批签名与 Content Hash 绑定。

---

## 7. 建议的演进顺序

### P0：补齐规格正确性基础

1. 定义严格的 Requirement 和 Acceptance Schema。
2. 强制稳定唯一 ID 和 Requirement–Acceptance 映射。
3. 空 Acceptance Check 禁止通过。
4. 为初次发布增加审批与 Content Hash 绑定。
5. 将 Acceptance 命令放入真实受控沙箱。

### P1：补齐主流 SDD 主链

1. 引入 Project Constitution / Steering。
2. 增加正式 Design Spec 和 Design Gate。
3. 调整为 `Requirement → Design → Plan → DRG`。
4. 增加 Spec、Design、Plan、Task 跨产物一致性分析。
5. 让 Conformance 检查真实实现和测试语义，而不只检查文件存在。

### P2：增强存量演进和协作能力

1. 引入 Current Spec + Delta Spec。
2. 支持 ADDED/MODIFIED/REMOVED 语义。
3. 增加并行 ChangeSpec 冲突检测。
4. 建立跨 Spec 依赖图和 Requirement 所有权。
5. 支持跨项目、跨仓库的规格组合和执行。

---

## 8. 最终评价

当前系统已经完成了从"需求文档"到"运行时合同"的关键跨越：

```text
规格先发布
→ Run 固定绑定
→ 计划和执行图从规格派生
→ 节点产生 Artifact
→ Conformance 建立映射
→ Evidence 证明交付
→ 规格变化通过 SCR 和新版本接续
```

它最强的是规格版本治理、运行绑定和交付证据；最弱的是规格自身的表达精度、设计推导、语义一致性和多人并行演进。

一句话总结：

> 当前 SpecGraphflow 已经具备"Spec 驱动交付运行时"的骨架，但还需要补齐严格行为规约、Design Spec、Delta Spec、语义 Conformance 和项目级治理，才能达到成熟 Spec-Driven Development 系统的完整形态。
