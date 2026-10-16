# Agent 通用输入/输出 Schema

> 所有 Agent 的输入和输出必须遵守本文件定义的 Schema。调度器在调用 Agent 后会校验输出格式，不符则触发重试。

---

## 1. 通用输入 Schema

调度器构造的上下文包，注入到 Agent 会话：

```json
{
  "agent_definition": "agents/{agent_name}.md",
  "task": {
    "card_path": ".loop/active/{project}/task-xxx.md",
    "id": "task-xxx",
    "goal": "任务目标摘要",
    "constraints": ["约束1", "约束2"],
    "done_items": [
      {"id": "d1", "check": "...", "expect": "...", "status": "pending"}
    ]
  },
  "context_refs": [
    {
      "type": "artifact",
      "path": ".loop/artifacts/{project}/task-xxx/round-1/architecture.json",
      "description": "上游架构决策"
    },
    {
      "type": "shared",
      "path": "shared/api-contract.md",
      "description": "API 契约"
    },
    {
      "type": "blackboard",
      "path": "shared/blackboard.md",
      "description": "共享黑板摘要"
    }
  ],
  "memory_refs": {
    "project_brief": ".loop/memory/project-brief.md",
    "relevant_decisions": [".loop/memory/decision-log.md#ADR-003"],
    "relevant_failures": [".loop/memory/failure-memory/FM-001.md"],
    "agent_working_notes": "agents/{agent_name}/memory.md",
    "matched_recipes": [".loop/memory/recipes/crud-module.md"]
  },
  "output_schema": "agents/_schema.md#{agent_name}-output",
  "acceptance_criteria": "输出必须符合本 Schema，否则触发重试"
}
```

### 输入字段说明

| 字段 | 说明 |
|------|------|
| `agent_definition` | Agent 角色定义文件路径（注入为 system prompt） |
| `task.card_path` | 任务卡文件路径 |
| `task.goal` | 任务目标摘要 |
| `task.constraints` | 约束条件列表 |
| `task.done_items` | Done 验收项（含可执行 check） |
| `context_refs` | 上下文引用列表（路径，非内容） |
| `memory_refs` | 记忆引用（Phase 8 新增）。Context Pack Builder 动态组装，含 project_brief / relevant_decisions / relevant_failures / agent_working_notes / matched_recipes |
| `output_schema` | 输出格式要求（指向本文件对应章节） |

---

## 2. 通用输出 Schema

所有 Agent 输出必须包含以下基础字段：

```json
{
  "agent": "agent-name",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {
      "type": "code|json|diff|report|config",
      "path": ".loop/artifacts/{project}/task-xxx/round-1/output.json",
      "description": "产物说明"
    }
  ],
  "summary": "本次执行摘要（一句话）",
  "next_suggested": "建议的下一步动作（供调度器参考）",
  "experience_update": {
    "failure_memory_candidates": [
      {
        "symptom": "症状描述",
        "root_cause": "根因分析",
        "fix": "修复方案",
        "applies_to": ["场景标签"],
        "confidence": "high|medium|low"
      }
    ],
    "working_notes_update": {
      "new_patterns": ["新发现的可复用模式"],
      "new_cautions": ["新的注意事项"]
    },
    "recipe_candidate": null,
    "decision_candidate": null
  }
}
```

### 基础字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `agent` | 是 | Agent 名称 |
| `task_id` | 是 | 任务 ID |
| `round` | 是 | 第几轮 |
| `status` | 是 | `success` / `partial` / `failed` |
| `artifacts` | 是 | 产物列表（路径引用） |
| `summary` | 是 | 执行摘要 |
| `next_suggested` | 否 | 建议下一步（调度器可忽略） |
| `experience_update` | 否 | 经验更新候选（Phase 8 新增）。Agent 自报告经验，调度器经三级门禁验证后写入记忆库。无经验时省略此字段 |

---

## 3. 各 Agent 专属输出 Schema

### 3.1 SpecAgent 输出

```json
{
  "agent": "spec-agent",
  "task_id": "task-xxx",
  "round": 0,
  "status": "success",
  "artifacts": [
    {"type": "interview_script", "path": ".loop/artifacts/{project}/task-xxx/round-0/interview-script.md", "description": "客户访谈脚本（interview-script 固化，可选）"},
    {"type": "interview_summary", "path": ".loop/artifacts/{project}/task-xxx/round-0/interview-summary.md", "description": "访谈总结（summarize-interview 固化，可选）"},
    {"type": "meeting_summary", "path": ".loop/artifacts/{project}/task-xxx/round-0/meeting-summary.md", "description": "会议总结（summarize-meeting 固化，可选）"},
    {"type": "product_prd", "path": "projects/{product}/prd.md", "description": "产品需求文档（create-prd 固化，必选）"},
    {"type": "ost", "path": ".loop/artifacts/{project}/task-xxx/round-0/ost.md", "description": "机会解决方案树（opportunity-solution-tree 固化，可选）"},
    {"type": "assumptions_analysis", "path": ".loop/artifacts/{project}/task-xxx/round-0/assumptions-new.md", "description": "假设识别（identify-assumptions-new/existing 固化，可选）"},
    {"type": "assumption_prioritization", "path": ".loop/artifacts/{project}/task-xxx/round-0/assumption-prioritization.md", "description": "假设优先级排序（prioritize-assumptions 固化，可选）"},
    {"type": "job_stories", "path": ".loop/artifacts/{project}/task-xxx/round-0/job-stories.md", "description": "Job Stories（job-stories 固化，可选）"},
    {"type": "task_card", "path": ".loop/active/{project}/task-xxx.md", "description": "生成的任务卡"},
    {"type": "json", "path": ".loop/artifacts/{project}/task-xxx/round-0/spec.json", "description": "任务卡结构化 JSON 镜像（供调度器校验和下游 Agent 读取）"}
  ],
  "pm_discussion": {
    "skills_executed": ["summarize-interview", "create-prd"],
    "prd_sections_present": ["summary", "contacts", "background", "objective", "market_segments", "value_propositions", "solution", "release"]
  },
  "discovery": {
    "skills_executed": ["opportunity-solution-tree", "identify-assumptions-new", "prioritize-assumptions"],
    "scenario": "new_product",
    "ost_present": true,
    "assumption_categories_covered": ["value", "usability", "viability", "feasibility", "ethics", "go_to_market", "strategy", "team"]
  },
  "task_card": {
    "id": "task-xxx",
    "goal": "任务目标",
    "done_items": [
      {"id": "d1", "check": "命令", "expect": "预期", "status": "pending"}
    ],
    "constraints": ["约束1"],
    "dependencies": ["task-yyy"]
  },
  "summary": "将用户需求转化为任务卡"
}
```

### 3.2 ArchitectAgent 输出

```json
{
  "agent": "architect-agent",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {"type": "contract", "path": "shared/api-contract.md", "description": "API 契约"},
    {"type": "json", "path": ".loop/artifacts/{project}/task-xxx/round-1/architecture.json", "description": "架构决策"}
  ],
  "architecture": {
    "tech_stack": {"backend": "Fastify", "frontend": "React+Vite"},
    "directory_structure": "目录方案",
    "decisions": [
      {"decision": "选用 Fastify", "reason": "轻量高性能", "alternatives": ["Express"]}
    ],
    "risks": [{"risk": "描述", "mitigation": "缓解措施"}],
    "environment_requirements": [
      {"tool": "python", "min_version": "3.10", "required": true, "check_cmd": "py --version", "description": "后端运行时（Windows 用 py，Linux/Mac 用 python3）"},
      {"tool": "node", "min_version": "18.0", "required": true, "check_cmd": "node --version", "description": "前端构建"},
      {"tool": "postgresql", "min_version": "14", "required": false, "check_cmd": "psql --version", "description": "可选，Docker 替代"}
    ]
  },
  "summary": "确定技术选型和 API 契约"
}
```

#### architecture 字段说明（含 environment_requirements）

| 字段 | 必填 | 说明 |
|------|------|------|
| `tech_stack` | 是 | 含 backend/frontend 键 |
| `directory_structure` | 是 | 目录方案描述 |
| `decisions` | 是 | 至少 1 项，每项含 decision/reason |
| `risks` | 是 | 可为空数组 |
| `environment_requirements` | 是 | 环境需求清单，详见下方约束 |

**environment_requirements 字段约束**：

| 子字段 | 类型 | 必填 | 约束 |
|--------|------|------|------|
| `tool` | string | ✅ | 工具名（python/node/postgresql/cmake/...） |
| `min_version` | string | ✅ | 最低版本号（语义化版本，如 "3.10"、"18.0"） |
| `required` | bool | ✅ | true=必装（缺失 FAIL）；false=可选（缺失 WARN） |
| `check_cmd` | string | ✅ | 可在本机直接执行的版本检测命令 |
| `description` | string | 否 | 用途说明 |

> ENV_CHECK 阶段（Gate-Auto 环境预检模式）会读取本字段，逐项执行 `check_cmd` 并比对 `min_version`。详见 `agents/gate-auto.md` §11。

### 3.3 DevAgent 输出（BackendDevAgent / FrontendDevAgent 通用）

> ClientDevAgent（QT 客户端）有专属输出 Schema，见 §3.9。产物路径按项目类型替换：WEB 用 `projects/web-app/`，QT 客户端用 `projects/pc-client/`。

```json
{
  "agent": "backend-dev",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {"type": "diff", "path": ".loop/artifacts/{project}/task-xxx/round-1/backend.diff", "description": "代码变更 patch"},
    {"type": "test", "path": "projects/web-app/backend/src/tests/", "description": "单元测试"}
  ],
  "changes": {
    "files_modified": ["src/routes/user.ts", "src/services/userService.ts"],
    "files_added": ["src/tests/user.test.ts"],
    "files_deleted": [],
    "commit_message": "feat: implement user creation API"
  },
  "test_coverage": 85,
  "summary": "实现用户创建 API，含单元测试"
}
```

### 3.4 TestAgent 输出

```json
{
  "agent": "test-agent",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {"type": "report", "path": ".loop/artifacts/{project}/task-xxx/round-1/test-report.json", "description": "测试报告"}
  ],
  "test_result": {
    "total": 20,
    "passed": 18,
    "failed": 2,
    "skipped": 0,
    "coverage": 78,
    "failures": [
      {"test": "should create user with valid data", "reason": "Expected 201, got 400"}
    ]
  },
  "summary": "18/20 测试通过，覆盖率 78%"
}
```

### 3.5 ReviewAgent 输出

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
  "l3_trigger_hints": [],
  "next_action": "RETRY_BACKEND_DEV",
  "summary": "发现 1 个 critical + 1 个 warning，拒绝"
}
```

### 3.6 DevOpsAgent 输出

```json
{
  "agent": "devops-agent",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {"type": "log", "path": ".loop/artifacts/{project}/task-xxx/round-1/deploy-result.json", "description": "部署结果"}
  ],
  "build": {"success": true, "duration_seconds": 45, "image_tag": "v1.0.0"},
  "deploy": {"success": true, "environment": "dev", "url": "http://localhost:3000"},
  "summary": "构建成功，部署到 dev 环境"
}
```

### 3.7 Gate-Auto 输出（L1 自动闸门）

```json
{
  "agent": "gate-auto",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {"type": "report", "path": ".loop/artifacts/{project}/task-xxx/round-1/gate-result.json", "description": "闸门结果"}
  ],
  "gate_result": {
    "verdict": "PASS",
    "total_checks": 5,
    "passed": 5,
    "failed": 0,
    "results": [
      {"id": "d1", "check": "curl ...", "expect": "ok", "actual": "ok", "passed": true},
      {"id": "d2", "check": "npm test", "expect": "regex:\\d+ passing", "actual": "10 passing", "passed": true}
    ]
  },
  "next_action": "AWAITING_REVIEW",
  "summary": "L1 自动闸门全部通过"
}
```

### 3.8 UIDesignAgent 输出

```json
{
  "agent": "ui-design-agent",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {"type": "design", "path": ".loop/artifacts/{project}/task-xxx/round-1/ui-design.json", "description": "UI 设计规约"}
  ],
  "ui_design": {
    "pages": [
      {
        "name": "HomePage",
        "route": "/",
        "layout": "header + main + footer",
        "components": ["Header", "HeroSection", "FeatureList", "Footer"],
        "states": ["loading", "error", "empty", "success"],
        "interactions": ["点击特性卡片跳转详情"]
      }
    ],
    "component_inventory": [
      {
        "name": "Header",
        "type": "reusable",
        "props": {"title": "string", "navItems": "NavItem[]"},
        "variants": ["default", "scrolled"]
      }
    ],
    "design_tokens": {
      "design_system": "huaDesign",
      "tokens_ref": "dahua-direction/UI/tokens/hua_design_tokens.json",
      "colors": {
        "primary": "color.primary.6",
        "background": "color.neutral.bg",
        "surface": "color.neutral.bgCard",
        "text_primary": "color.neutral.fontR",
        "text_secondary": "color.neutral.fontS",
        "border": "color.neutral.split",
        "warning": "color.warning.6",
        "success": "color.success.6",
        "error": "color.error.6"
      },
      "spacing": {
        "grid_unit": "spacing.x1",
        "scale": ["spacing.x1", "spacing.x2", "spacing.x3", "spacing.x4", "spacing.x6", "spacing.x10", "spacing.x16"]
      },
      "typography": {
        "body": "font.fontSize.body",
        "small": "font.fontSize.small",
        "subtitle": "font.fontSize.subtitle",
        "title": "font.fontSize.title",
        "display": "font.fontSize.display",
        "h1": "font.fontSize.h1",
        "figures": "font.figures.fontVariantNumeric"
      },
      "radius": {"small": "radius.small", "default": "radius.default", "large": "radius.large"},
      "shadow": {
        "level1": "shadow.level1.boxShadow",
        "level2": "shadow.level2.boxShadow",
        "level3": "shadow.level3.boxShadow"
      },
      "breakpoints": {"mainstream": "layout.resolutions.mainstream", "legacy": "layout.resolutions.legacy"}
    },
    "accessibility": ["键盘导航", "ARIA 标签", "色彩对比度 ≥ 4.5:1"]
  },
  "summary": "定义 N 个页面、N 个组件、设计规范"
}
```

#### ui_design 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `pages` | 是 | 页面列表，每个页面含路由、布局、组件引用、状态、交互 |
| `pages[].states` | 是 | 必须包含 loading / error / empty / success 四种状态 |
| `component_inventory` | 是 | 组件清单，区分 reusable 和 page-specific |
| `component_inventory[].props` | 是 | 组件 props 类型定义（TypeScript 风格） |
| `design_tokens` | 是 | huaDesign token 引用层。须含 `design_system:"huaDesign"` + `tokens_ref` + `colors`/`spacing`/`typography`/`radius`/`shadow`/`breakpoints`，**值为 token dot-path 引用**（如 `"color.primary.6"`），禁止裸 hex / 裸 px。确定性层 `schema.py:validate_ui_design_contract` 校验单一主色 = `color.primary.6`、colors 值为合法 token 路径、spacing 引用 4n token |
| `accessibility` | 是 | 可访问性要求清单 |

> **QT 模式**（`platform: "qt"`）：输出字段为 `windows` / `widget_inventory` / `signals_slots` / `qss_tokens` / `accessibility`，详见 `agents/ui-design-agent.md` QT 模式示例。`qss_tokens` 同样遵循 huaDesign token 引用契约（`design_system` + `colors`/`spacing` 引用 token dot-path）。

---

### 3.9 ClientDevAgent 输出

```json
{
  "agent": "client-dev",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {"type": "diff", "path": ".loop/artifacts/{project}/task-xxx/round-1/client.diff", "description": "代码变更 patch"},
    {"type": "code", "path": "projects/pc-client/src/", "description": "QT 客户端代码"}
  ],
  "changes": {
    "files_modified": ["src/widgets/MainWindow.cpp", "src/widgets/MainWindow.h"],
    "files_added": ["src/widgets/LoginDialog.ui", "src/widgets/LoginDialog.cpp", "src/styles/theme.qss", "CMakeLists.txt"],
    "files_deleted": [],
    "commit_message": "feat: implement main window with login dialog"
  },
  "qt_artifacts": {
    "ui_files": ["src/widgets/LoginDialog.ui"],
    "qrc_files": ["src/resources/resources.qrc"],
    "qss_files": ["src/styles/theme.qss"],
    "cmake": "CMakeLists.txt"
  },
  "summary": "实现主窗口 + 登录对话框，含 QSS 样式"
}
```

#### qt_artifacts 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `ui_files` | 否 | .ui 文件列表（Qt Designer XML，由 uic 处理） |
| `qrc_files` | 否 | .qrc 资源文件列表（Qt 资源系统） |
| `qss_files` | 否 | .qss 样式表文件列表（Qt Style Sheets） |
| `cmake` | 是 | CMakeLists.txt 路径（CMake 构建配置） |

---

### 3.10 Orchestrator-LLM 输出（任务分配决策）

```json
{
  "agent": "orchestrator-llm",
  "task_id": "task-xxx",
  "round": 0,
  "status": "success",
  "artifacts": [
    {"type": "assignment", "path": ".loop/artifacts/{project}/task-xxx/round-0/assignment.json", "description": "任务分配决策"}
  ],
  "assignment": {
    "assigned_to": "architect-agent",
    "project_type": "web",
    "subtasks": [
      {
        "id": "task-001",
        "name": "骨架+登录",
        "assigned_to": ["ui-design-agent", "backend-dev", "frontend-dev", "test-agent", "devops-agent"],
        "dependencies": [],
        "parallel_group": 1
      },
      {
        "id": "task-002",
        "name": "进度监控",
        "assigned_to": ["ui-design-agent", "backend-dev", "frontend-dev", "test-agent", "devops-agent"],
        "dependencies": ["task-001"],
        "parallel_group": 2
      }
    ],
    "execution_order": [
      {"step": 1, "agent": "architect-agent", "parallel_with": null},
      {"step": 2, "agent": "ui-design-agent", "parallel_with": "backend-dev"},
      {"step": 3, "agent": "frontend-dev", "depends_on": "ui-design-agent"},
      {"step": 4, "agent": "test-agent", "barrier": "all_dev_agents_done"},
      {"step": 5, "agent": "gate-auto"},
      {"step": 6, "agent": "review-agent"},
      {"step": 7, "agent": "devops-agent"}
    ]
  },
  "summary": "分配 4 个子任务，2 组并行"
}
```

#### assignment 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `assigned_to` | 是 | 首个调度的 Agent |
| `project_type` | 是 | `web` / `qt`（决定前端/客户端 Agent 选择） |
| `subtasks` | 是 | 子任务列表，每个含 id/name/assigned_to/dependencies/parallel_group |
| `subtasks[].assigned_to` | 是 | **S-2 修复**：子任务内全部需要通过 PENDING 分配的 Agent 列表（按执行顺序），必须包含 `ui-design-agent` / `backend-dev` / `frontend-dev`(或 `client-dev`) / `test-agent` / `devops-agent`。**不包含** `gate-auto` / `review-agent`（由状态机在 AWAITING_AUTO_GATE / AWAITING_REVIEW 自动触发，由调度器追加到 executed_agents，见 F-4 修复） |
| `execution_order` | 是 | 执行顺序，含 step/agent/parallel_with/depends_on/barrier（串行模式下为参考，实际按 subtasks[].assigned_to 顺序执行） |

---

### 3.11 JSON 产物字段约束总表

所有 Agent 输出的 JSON 产物必须符合以下字段约束。调度器校验时逐项检查，**任一不符拒绝接受**：

#### spec.json（SpecAgent）

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| agent | string | ✅ | 固定 "spec-agent" |
| task_id | string | ✅ | 格式 "task-NNN" |
| round | int | ✅ | 固定 0 |
| status | string | ✅ | "success" / "partial" / "failed" |
| artifacts | array | ✅ | 至少 3 项（product_prd 路径 + task_card 路径 + spec.json 路径），可选含 interview_script/interview_summary/meeting_summary/ost/assumptions_analysis/assumption_prioritization/job_stories |
| pm_discussion | object | ✅ | PM Discussion 执行记录 |
| pm_discussion.skills_executed | array | ✅ | 至少含 "create-prd" |
| pm_discussion.prd_sections_present | array | ✅ | 必须含全部 8 段（summary/contacts/background/objective/market_segments/value_propositions/solution/release） |
| discovery | object | 否 | Discovery & Assumptions 执行记录（opt-in：含此字段时触发校验） |
| discovery.skills_executed | array | ✅ | 非空，含至少一个 skill 名 |
| discovery.scenario | string | 否 | `new_product` / `existing_product` |
| discovery.ost_present | bool | 否 | 是否产出了 OST |
| discovery.assumption_categories_covered | array | 否 | 覆盖的风险类别列表 |
| assumptions[]（结构化，opt-in） | object | 否 | 当 assumption 项含 `category` 字段时触发结构化校验 |
| assumptions[].id | string | ✅ | 格式 "ASM-NNN" |
| assumptions[].category | string | ✅ | `value` / `usability` / `viability` / `feasibility` / `ethics` / `go_to_market` / `strategy` / `team` |
| assumptions[].description | string | ✅ | 非空 |
| assumptions[].confidence | string | ✅ | `high` / `medium` / `low` |
| assumptions[].test_method | string | ✅ | 非空 |
| assumptions[].priority | string | 否 | `high_impact_low_risk` / `high_impact_high_risk` / `low_impact_low_risk` / `low_impact_high_risk` |
| assumptions[].experiment | string | 否 | `high_impact_high_risk` 时必填 |
| assumptions[].source | string | ✅ | `identify-assumptions-new` / `identify-assumptions-existing` / `manual` / `legacy-task-card` / `spec-change-request` |
| requirements[].job_story（opt-in） | object | 否 | 当 requirement 项含 `job_story` 字段时触发结构化校验 |
| requirements[].job_story.situation | string | ✅ | "When..." 部分，用户触发情境，非空 |
| requirements[].job_story.motivation | string | ✅ | "I want..." 部分，用户动机，非空 |
| requirements[].job_story.outcome | string | ✅ | "so I can..." 部分，期望结果，非空 |
| task_card | object | ✅ | 含 id/goal/done_items/constraints/dependencies |
| task_card.done_items | array | ✅ | 每项含 id/check/expect/status |
| task_card.done_items[].check | string | ✅ | 可执行 shell 命令，非空 |
| task_card.done_items[].expect | string | ✅ | 非空，含匹配模式前缀 |
| summary | string | ✅ | 非空 |

#### architecture.json（ArchitectAgent）

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| agent | string | ✅ | 固定 "architect-agent" |
| architecture | object | ✅ | 含 tech_stack/directory_structure/decisions/risks/environment_requirements |
| architecture.tech_stack | object | ✅ | 含 backend/frontend 键 |
| architecture.decisions | array | ✅ | 至少 1 项，每项含 decision/reason |
| architecture.risks | array | ✅ | 可为空数组 |
| architecture.environment_requirements | array | ✅ | 至少 1 项，每项含 tool/min_version/required/check_cmd |
| architecture.environment_requirements[].required | bool | ✅ | true=必装；false=可选 |

#### ui-design.json（UIDesignAgent）

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| agent | string | ✅ | 固定 "ui-design-agent" |
| ui_design | object | ✅ | 含 platform 字段 |
| ui_design.platform | string | ✅ | "web" / "qt" |
| ui_design.pages（web） | array | ✅ | 至少 1 页，每页含 name/route/layout/components/states/interactions |
| ui_design.pages[].states | array | ✅ | 必须含 loading/error/empty/success 四种 |
| ui_design.component_inventory | array | ✅ | 至少 1 项，含 name/type/props |
| ui_design.design_tokens | object | ✅ | huaDesign token 引用层：含 design_system/tokens_ref + colors/spacing/typography/radius/shadow/breakpoints，值为 token dot-path（确定性层校验单一主色=color.primary.6、禁止裸值、4n spacing） |
| ui_design.accessibility | array | ✅ | 至少 3 项 |
| ui_design.windows（qt） | array | ✅ | 至少 1 窗口，含 name/class/layout/widgets/states |
| ui_design.signals_slots（qt） | array | ✅ | 至少 1 项，含 signal/slot |
| ui_design.qss_tokens（qt） | object | ✅ | huaDesign token 引用层：含 design_system/tokens_ref + colors/spacing 引用 token dot-path（fonts 可保留平台字体名，不强校验） |

#### backend.diff / frontend.diff / client.diff（DevAgent）

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| agent | string | ✅ | "backend-dev" / "frontend-dev" / "client-dev" |
| artifacts | array | ✅ | 含 diff 路径 |
| changes | object | ✅ | 含 files_modified/files_added/files_deleted/commit_message |
| changes.files_modified | array | ✅ | 可为空数组 |
| changes.files_added | array | ✅ | 至少 1 项（新任务必须有新增文件） |
| changes.commit_message | string | ✅ | 符合 conventional commits 格式 |
| test_coverage（backend） | int | ✅ | 0-100，>= 80 为合格 |
| qt_artifacts（client） | object | ✅ | 含 cmake 字段（.ui/.qrc/.qss 可选） |

#### test-report.json（TestAgent）

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| agent | string | ✅ | 固定 "test-agent" |
| test_result | object | ✅ | 含 total/passed/failed/skipped/coverage/failures |
| test_result.total | int | ✅ | >= 1 |
| test_result.passed | int | ✅ | >= 0 |
| test_result.failed | int | ✅ | >= 0 |
| test_result.coverage | int | ✅ | 0-100 |
| test_result.failures | array | ✅ | failed > 0 时必须非空，每项含 test/reason |

#### gate-result.json（Gate-Auto）

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| agent | string | ✅ | 固定 "gate-auto" |
| gate_result | object | ✅ | 含 verdict/total_checks/passed/failed/results |
| gate_result.verdict | string | ✅ | "PASS" / "FAIL" |
| gate_result.total_checks | int | ✅ | >= 1 |
| gate_result.passed | int | ✅ | passed + failed = total_checks |
| gate_result.results | array | ✅ | 每项含 id/check/expect/actual/passed |

#### review.json（ReviewAgent）

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| agent | string | ✅ | 固定 "review-agent" |
| verdict | string | ✅ | "APPROVE" / "REJECT" |
| issues | array | ✅ | verdict=REJECT 时必须非空 |
| issues[].severity | string | ✅ | "critical" / "warning" / "info" |
| issues[].file | string | ✅ | 非空 |
| issues[].rule | string | ✅ | 非空 |
| issues[].suggestion | string | ✅ | 非空 |
| approve_conditions | array | 否 | verdict=APPROVE 时可空 |
| l3_trigger_hints | array | 否 | L3 人工抽查触发提示（如 architecture_change/ui_design_change/security_related），空数组表示无需 L3 抽查 |

#### deploy-result.json（DevOpsAgent）

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| agent | string | ✅ | 固定 "devops-agent" |
| build | object | ✅ | 含 success/duration_seconds |
| build.success | bool | ✅ | true / false |
| deploy | object | ✅ | 含 success/environment |
| deploy.success | bool | ✅ | DONE 前必须为 true |

#### assignment.json（Orchestrator-LLM）

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| agent | string | ✅ | 固定 "orchestrator-llm" |
| assignment | object | ✅ | 含 assigned_to/project_type/subtasks/execution_order |
| assignment.project_type | string | ✅ | "web" / "qt" |
| assignment.subtasks | array | ✅ | 至少 1 项 |
| assignment.execution_order | array | ✅ | 至少 7 步（含全部必跑 Agent） |

---

## 4. Schema 校验规则（硬性强制）

调度器在 Agent 返回输出后执行以下校验。**所有校验均为硬性强制，任一不通过禁止状态转换**：

1. **基础字段校验**：`agent`、`task_id`、`round`、`status`、`artifacts`、`summary` 必须存在
2. **Agent 专属字段校验**：对应 §3 中的专属字段必须存在
3. **status 值域**：只能是 `success` / `partial` / `failed`
4. **artifacts 路径校验**：路径必须存在于文件系统中（**硬性**，落盘失败禁止转换）
5. **verdict 值域**（ReviewAgent）：只能是 `APPROVE` / `REJECT`
6. **产物完整性校验**（新增，硬性）：Agent 输出的 artifact 必须落盘到 `.loop/artifacts/{task_id}/round-{N}/` 对应路径，文件存在且非空
7. **Agent 覆盖率校验**（新增，硬性）：转换到 DONE 前，任务卡 `executed_agents` 必须含全部必跑 Agent（见 `.loop/checklists/layer-0-must-run.md`）。**H-1 修复**：executed_agents 按子任务分区（`executed_agents_arch` + `executed_agents_subtask`），架构阶段覆盖校验用 arch 列表，开发阶段校验 arch + 当前子任务列表。**C-2 修复**：DONE 产物校验按 `phase` 区分（架构阶段 7 项，开发阶段 14 项）
8. **experience_update 校验**（Phase 8 新增，可选）：若 `experience_update` 字段存在，校验子字段格式——`failure_memory_candidates` 每项含 symptom/root_cause/fix/applies_to/confidence；`working_notes_update` 含 new_patterns/new_cautions 数组。格式不符不触发重试，仅丢弃该候选并记录日志

### 4.1 校验失败处理（硬性，不可绕过）

| 校验项 | 失败动作 | 重试上限 |
|--------|---------|---------|
| 基础字段缺失 | 触发重试（`output_schema_violation`） | 2 |
| 专属字段缺失 | 触发重试 | 2 |
| status 值域违规 | 触发重试 | 2 |
| artifacts 路径不存在 | **拒绝状态转换**，触发重试 | 2 |
| verdict 值域违规 | 触发重试 | 2 |
| **产物完整性校验失败** | **拒绝 transition_to()，状态保持当前值**，记录违规日志 | max_retry(3) |
| **Agent 覆盖率校验失败** | **拒绝 transition_to(DONE)**，强制回退到缺失 Agent 重跑 | max_retry(3) |

### 4.2 硬性强制原则

- **Layer 0 人工模式同样适用**，AI 充当调度器时不得绕过任何校验
- **不允许选择性执行**：所有校验项必须全部通过才能转换状态
- **不满足条件不接受下一阶段工作**：任一校验失败，调度器拒绝推进
- 校验逻辑详见 `.loop/state-machine.md` §5 `transition_to()` 实现

---

## 5. Spec Snapshot Requirement/Acceptance Schema

> Spec Snapshot 的 `requirements` 和 `acceptance_criteria` 结构由 `schema_version` 门控。`1.1` 强制 EARS + GWT + 显式映射；`1.0` 向后兼容（legacy 转换、存量数据）。确定性层校验在 `scheduler/specs.py:validate_strict_requirement_acceptance`。

### 5.1 schema_version 门控策略

| 版本 | 校验策略 | 适用场景 |
|------|---------|---------|
| `1.0` | 仅查字段存在 + open_questions 清空 + requirements/acceptance 非空 | legacy 转换、存量数据、测试 stub |
| `1.1` | 1.0 全部 + EARS 结构 + GWT 结构 + 显式映射 + 类型条件 | SpecAgent 新产出、revision draft |

SpecAgent 默认产出 `1.1`。`build_snapshot_payload` 和 `create_revision_draft_from_scr` 会从 ChangeSpec / 基线 Snapshot 继承 `schema_version`，不硬编码。

### 5.2 Requirement Schema（1.1）

```json
{
  "id": "REQ-001",
  "type": "functional",
  "priority": "MUST",
  "actor": "end_user",
  "condition": "when the user submits login credentials",
  "system_response": "the system shall authenticate the user and enter the main workspace",
  "source_refs": ["projects/web-app/prd.md#login"],
  "tags": ["auth", "ui"],
  "job_story": {
    "situation": "when I need to access my workspace quickly",
    "motivation": "I want to log in with a single action",
    "outcome": "so I can start working without delay"
  }
}
```

| 字段 | 必需 | 说明 |
|------|------|------|
| `id` | 是 | 稳定唯一 ID，revision 不可改名 |
| `type` | 是 | `functional` / `non_functional` / `constraint` / `ui` / `data` / `security` |
| `priority` | 是 | `MUST` / `SHOULD` / `MAY`（RFC 2119） |
| `actor` | 是 | 行为主体，非空 |
| `condition` | 是 | 触发条件，非空 |
| `system_response` | 是 | 系统应产生的行为（"the system shall ..."），非空 |
| `source_refs` | 否 | 来源引用列表 |
| `tags` | 否 | 分类标签列表 |
| `job_story` | 否 | Job Story 结构（`situation` + `motivation` + `outcome`），与 EARS 互补。opt-in：当存在时触发结构化校验（`scheduler/spec_flow.py`） |

### 5.3 Acceptance Schema（1.1）

```json
{
  "id": "ACC-001",
  "requirement_ids": ["REQ-001"],
  "type": "automated",
  "scenario": "valid login enters workspace",
  "given": ["user is on login page"],
  "when": ["user submits valid credentials"],
  "then": ["system authenticates and redirects to workspace"],
  "check": "npm test -- --grep login",
  "expect": "pass",
  "evidence_required": ["test-result"]
}
```

| 字段 | 必需 | 说明 |
|------|------|------|
| `id` | 是 | 稳定唯一 ID |
| `requirement_ids` | 是 | 非空数组，引用存在的 Requirement（M:N） |
| `type` | 是 | `automated` / `manual` / `review` / `performance` / `security` |
| `given` | 是 | 前置条件，非空字符串数组 |
| `when` | 是 | 动作，非空字符串数组 |
| `then` | 是 | 预期结果，非空字符串数组 |
| `evidence_required` | 是 | 证据类型数组（`test-result` / `review-result` / `ui-review` 等） |
| `scenario` | 否 | 验收场景名称 |
| `check` | 条件 | automated 类型必须非空 |
| `expect` | 条件 | automated 类型必须非空 |

### 5.4 类型条件校验

| type | 附加必需字段 |
|------|-------------|
| `automated` | `check` + `expect` 非空 |
| `manual` | `evidence_required` + `reviewer` 或 `review_scope` |
| `review` | `review_scope` |
| `performance` | `metric` + `threshold` |
| `security` | `threat` + `control`，或 `check`，或 `review_scope` |

### 5.5 映射完整性

- 禁止孤儿 Requirement：每个 Requirement 至少被一个 Acceptance 覆盖
- 禁止孤儿 Acceptance：`requirement_ids` 必须引用存在的 Requirement ID
- 映射基数 M:N：一个 Acceptance 可验证多个 Requirement，一个 Requirement 可被多个 Acceptance 覆盖

### 5.6 Conformance Trace Matrix

ConformanceReport 新增 `trace_matrix` 字段（与现有 `requirements` / `acceptance_criteria` 扁平列表并存），结构化追踪矩阵：

```json
{
  "requirement_id": "REQ-001",
  "priority": "MUST",
  "acceptance_ids": ["ACC-001", "ACC-002"],
  "implementation_nodes": ["wp-req-001_implement"],
  "test_nodes": ["wp-req-001_test"],
  "evidence_refs": [".loop/runs/run-001/artifacts/wp-req-001_implement.json"],
  "status": "PASS",
  "reason": ""
}
```

| status | 语义 | 触发 |
|--------|------|------|
| `PASS` | 有证据且验收映射完整 | 节点完成 + artifact 存在 + acceptance 非空 |
| `UNVERIFIED` | 待证据（实现未完成） | 节点未完成 / artifact 不存在 |
| `FAIL` | 验收本身缺失（spec 缺陷） | requirement 无 acceptance 映射 / automated acceptance 空检查 |

`overall` 判定（1.1 priority-aware）：
- `MUST` FAIL → overall = FAIL（阻断）
- `MUST` UNVERIFIED → overall = UNVERIFIED（阻断）
- `SHOULD` FAIL/UNVERIFIED → 不阻断 overall=PASS（记入 issues）
- `MAY` FAIL/UNVERIFIED → 不阻断不记 issue

### 5.7 禁止空验收自动 PASS（全局安全底线）

无论 1.0 还是 1.1：
- Requirement 无任何 Acceptance 映射 → **不 PASS**（1.1 = FAIL，1.0 = UNVERIFIED）
- automated Acceptance 的 `check` 或 `expect` 为空 → **FAIL**
- 有文件存在不等于 PASS —— 必须验收映射完整 + 证据存在

---

## 6. Design Spec Schema

> Design Spec 是 Requirement 和 ExecutionPlan 之间的设计合同。它回答："为了实现这些 Requirements，系统准备怎么设计？"

### 6.1 定位

```text
PRD / Requirement = 要做什么
Design Spec       = 准备怎么做（设计合同）
ExecutionPlan     = 拆成哪些工作包
DRG               = 节点如何执行
Implementation    = 真实修改代码
```

### 6.2 schema_version 门控策略

| 版本 | 校验策略 | 适用场景 |
|------|---------|---------|
| `1.0` | 字段存在性 + open_questions 清空 + traceability 非空 | 确定性 fallback、P0 |
| `1.1`（未来） | 1.0 全部 + 严格 traceability + approval hash 绑定 | P2 审批链路 |

P0 使用 `1.0`。确定性 fallback 自动生成 `1.0` 最小 design-spec.json。

### 6.3 Design Spec Schema（1.0）

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
    "source": ".loop/runs/run-001/artifacts/design-spec.json",
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

### 6.4 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `schema_version` | 是 | `"1.0"`（P0） |
| `project` | 是 | 项目名 |
| `spec_id` | 是 | 绑定的 SpecSnapshot spec_id |
| `spec_version` | 是 | 绑定的 SpecSnapshot version |
| `design_id` | 是 | 设计 ID，格式 `design-{spec_id}-v{version}` |
| `status` | 是 | `DRAFT` / `REVIEWED` / `APPROVED` |
| `source_snapshot` | 是 | 绑定的 SpecSnapshot 引用（spec_id + version + content_hash） |
| `architecture` | 是 | 架构摘要：summary + affected_modules + component_boundaries |
| `architecture.component_boundaries[]` | 是 | 组件边界：name + type + responsibilities + requirements |
| `api_contracts` | 否 | API 契约列表（P0 可为空数组） |
| `data_model` | 否 | 数据模型列表（P0 可为空数组） |
| `ui_contract` | 否 | UI 契约引用（P0 可为空对象） |
| `state_transitions` | 否 | 状态转换列表（P0 可为空数组） |
| `error_handling` | 否 | 错误处理列表（P0 可为空数组） |
| `test_strategy` | 是 | 测试策略列表：每条 acceptance 对应一个测试策略 |
| `traceability` | 是 | 需求到设计的追踪矩阵 |
| `traceability[]` | 是 | requirement_id + design_refs（非空）+ acceptance_ids |
| `open_questions` | 是 | 未解决问题列表（design_gate 要求为空） |
| `risks` | 否 | 风险列表（launch_blocking 项必须有 mitigation） |
| `approval` | 是 | 审批状态（P0 为 PENDING） |

### 6.5 Design Gate 校验规则

`design_gate_node` 执行以下检查（`scheduler/design_spec.py:run_design_gate_checks`）：

| 检查 ID | 规则 | 触发条件 |
|---------|------|---------|
| `design_spec.valid` | design-spec.json 校验通过 | 文件存在但字段缺失/类型错误 |
| `design_gate.open_questions_resolved` | `open_questions` 为空 | 有未解决问题 |
| `design_gate.requirement_has_design_refs` | 每个 traceability 条目有 `design_refs` | requirement 无设计引用 |
| `design_gate.source_hash_matches` | `source_snapshot.content_hash` = bound spec hash | hash 不匹配 |
| `design_gate.launch_blocking_risks_mitigated` | launch_blocking 风险有 mitigation | 未缓解的阻断风险 |

**opt-in 哲学**：当 `design-spec.json` 不存在时，design_gate 自动 PASS（返回空检查列表），与 `intent_audit` NO_INTENT_DOCS 同一哲学。确定性 fallback 总是生成 design-spec.json，因此实际执行中 gate 总是有内容可校验。

### 6.6 DRG 链路位置

```text
plan_gate → architecture_impact → architecture_gate
  → design_spec（NEW：聚合 arch + snapshot，产出 design-spec.json）
  → design_gate（NEW：校验 traceability/hash/open_questions）
  → drg_compile → implementation WP → ...
```

### 6.7 确定性 fallback

当无 LLM architect agent 注册时，`scheduler/design_spec.py:generate_minimal_design_spec` 自动生成最小 design-spec.json：

- 从 SpecSnapshot 读取 requirements / acceptance_criteria / product_shape
- 从 `architecture_impact_node.json` artifact 读取架构决策（如存在）
- 每个 requirement 生成一个 component_boundary + traceability 条目
- `open_questions` 为空，`source_snapshot.content_hash` 复制 bound spec hash
- 满足全部 design_gate 检查

### 6.8 资产路径

| 资产 | 路径 | 说明 |
|------|------|------|
| run-local design-spec.json | `.loop/runs/{run_id}/design-spec.json` | 当前 GraphRun 绑定的设计规格 |
| run-local design gate result | `.loop/runs/{run_id}/artifacts/design_gate_node.json` | 设计门校验结果 |
| 可复用 design-spec.json | `specs/{project}/designs/{spec_id}/v{N}/design-spec.json` | P2 审批链路 |
| 可复用 design-review.json | `specs/{project}/designs/{spec_id}/v{N}/design-review.json` | P2 审批证据 |

---

## 7. ExecutionPlan Design-Spec-Aware (P1)

> P1：`drg_compile_node` 在执行时从 Design Spec 派生更精细的 WP，替代初始从 Requirement 直接生成的粗粒度计划。

### 7.1 触发条件

`drg_compile_node` 执行时调用 `scheduler/plans.py:refine_plan_from_design_spec`，该方法通过 `is_rich_design_spec` 判断 design-spec.json 是否"丰富"：

| 条件 | 说明 |
|------|------|
| `api_contracts` 非空 | 架构师定义了 API 契约 |
| `data_model` 非空 | 架构师定义了数据模型 |
| `component_boundaries` 数量 ≠ `traceability` 数量 | 边界数与需求数不一致（合并或拆分） |

确定性 fallback（minimal design spec）**不触发** refine — `api_contracts`/`data_model` 为空且 boundaries 与 traceability 1:1 对应。

### 7.2 WP 派生规则

| Design Spec 对象 | 派生规则 | WP ID | Agent |
|------------------|---------|-------|-------|
| `component_boundaries[]` | 每个 boundary 一个 WP | `wp-{slug(name)}` | 由 `boundary_agent(type)` 映射 |
| `api_contracts[]` | 合并为一个 WP | `wp-api` | `backend-dev` |
| `data_model[]` | 合并为一个 WP | `wp-data` | `backend-dev` |
| 未覆盖的 Requirement | fallback WP | `wp-{slug(req_id)}` | `implementation_agent(template_id)` |

每个 refined WP 标记 `planning_basis: "design_spec"`；fallback WP 标记 `planning_basis: "requirement_ears"`。

### 7.3 boundary_agent 类型映射

| boundary type（包含子串） | Agent |
|---------------------------|-------|
| `frontend` / `ui` | `frontend-dev` |
| `backend` / `data` / `service` / `api` | `backend-dev` |
| 其他 | `implementation_agent(template_id)`（qt→client-dev, web→frontend-dev） |

### 7.4 Graph 热更新

`executor.run()` 在 `drg_compile_node` 完成后重新 `load_graph(run_id)`，更新 `limit`：

- Governance 头部节点 ID 固定（`spec_binding_node` ~ `drg_compile_node`）→ `state.completed_nodes` 仍有效
- WP 节点更新（新 WP ID）→ 未完成的节点会被执行
- Governance 尾部节点 ID 固定（`final_gate_node` ~ `done_node`）→ 不受影响

### 7.5 执行流程

```text
generate_execution_plan（外部，从 Requirement 生成初始 plan）
  → compile_execution_graph（外部，编译初始 graph）
  → executor.run
    → ... → design_spec_node（生成 design-spec.json）
    → design_gate_node（校验）
    → drg_compile_node（🔥 P1：refine plan + recompile graph）
    → [graph 热更新] → implementation WPs（从 design spec 派生）
    → final_gate → conformance → review → deploy → done
```

### 7.6 drg_compile_node 产物

`artifacts/drg_compile_node.json` 新增字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `plan_refined` | bool | 是否触发了 plan refine |
| `wp_count` | int / null | refined 后的 WP 数量（未 refine 时为 null） |

### 7.7 向后兼容

- 确定性 fallback design spec → `is_rich_design_spec` 返回 False → 不 refine → graph 不变 → 现有 94 测试全绿
- refine 后的 plan 经 `validate_execution_plan_payload` 校验，若有 critical/error issue 则回退到原 plan
- `compile_execution_graph` 若失败（plan issue），旧 graph 留在磁盘，executor 继续用旧 graph
