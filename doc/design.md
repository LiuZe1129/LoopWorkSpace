# Multi-Agent 系统设计概要

> 本文档总结了构建 Multi-Agent 系统时需要考虑的核心设计维度，作为架构参考。

## 目录

- [1. 全景概览](#1-全景概览)
- [2. Agent 角色定义与职责划分](#2-agent-角色定义与职责划分)
- [3. 通信与协调机制](#3-通信与协调机制)
- [4. 任务分解与编排](#4-任务分解与编排)
- [5. 上下文窗口与会话管理](#5-上下文窗口与会话管理)
- [6. 工具系统与资源管理](#6-工具系统与资源管理)
- [7. 错误处理与鲁棒性](#7-错误处理与鲁棒性)
- [8. 收敛与终止条件](#8-收敛与终止条件)
- [9. 可观测性（Observability）](#9-可观测性observability)
- [10. 安全与治理](#10-安全与治理)
- [11. 评估与测试](#11-评估与测试)
- [12. 优先级建议](#12-优先级建议)

---

## 1. 全景概览

构建一个 Multi-Agent 系统，Memory 只是冰山一角。以下是需要考虑的十大核心维度：

```
Multi-Agent System
├── Agent 设计
│   ├── 角色定义与职责划分
│   ├── LLM 选型与配置
│   ├── Persona & System Prompt
│   └── 能力声明 (Capabilities)
├── 通信与协调
│   ├── 消息协议
│   ├── 通信拓扑 (星型/网状/层级)
│   ├── 共享黑板模式
│   └── 事件驱动 vs 轮询
├── 任务编排
│   ├── 任务分解策略
│   ├── 调度器 / Orchestrator
│   ├── DAG 工作流 vs 自由对话
│   └── 动态任务分配
├── 状态管理
│   ├── 全局状态 vs 局部状态
│   ├── 上下文窗口管理
│   ├── 会话隔离
│   └── 状态持久化
├── 工具与能力
│   ├── 工具注册与发现
│   ├── 权限控制
│   ├── 工具调用冲突
│   └── 资源争用
├── 错误与鲁棒性
│   ├── 超时与重试
│   ├── Agent 崩溃恢复
│   ├── 幻觉检测
│   └── 死锁检测
├── 安全与治理
│   ├── Agent 间信任边界
│   ├── 敏感信息隔离
│   ├── 审计日志
│   └── 人类监督 (HITL)
├── 可观测性
│   ├── 全链路追踪
│   ├── 性能监控
│   ├── 成本追踪
│   └── 调试与回放
├── 收敛与终止
│   ├── 终止条件设计
│   ├── 无限循环防护
│   ├── 结果聚合与冲突解决
│   └── 质量评估
└── 扩展性
    ├── 水平扩展
    ├── Agent 热插拔
    └── 版本管理
```

---

## 2. Agent 角色定义与职责划分

这是系统的基石。每个 Agent 应该有清晰的边界。

| 设计要点 | 说明 | 关键问题 |
|---------|------|---------|
| **角色粒度** | 按职能拆分（Researcher、Coder、Reviewer）还是按领域拆分？ | 过细导致协调开销大，过粗失去多 Agent 优势 |
| **能力声明** | 每个 Agent 声明自己能做什么、不能做什么 | 类似接口契约，供 Orchestrator 调度 |
| **Persona 设计** | System Prompt 定义性格、专长、行为约束 | 影响 LLM 输出的风格和质量 |
| **LLM 配置** | 不同 Agent 可用不同模型（强模型做推理，弱模型做提取） | 成本与质量的平衡 |

**核心矛盾**：角色定义越清晰，协作越可控，但灵活性越低；角色越模糊，Agent 间容易出现"抢活"或"踢皮球"。

---

## 3. 通信与协调机制

这是 Multi-Agent 系统最核心的难点之一。

### 3.1 通信拓扑选择

#### 星型（Hub/Orchestrator）

```
        ┌── Agent A ──┐
        │             │
   Orchestrator       │
        │             │
        └── Agent B ──┘

优点：可控、可追踪
缺点：瓶颈、单点故障
```

#### 网状（Peer-to-Peer）

```
   Agent A ←────→ Agent B
      ↑                ↓
   Agent C ←────→ Agent D

优点：灵活、低延迟
缺点：混乱、难以追踪
```

### 3.2 需要做的设计决策

- **消息协议**：结构化消息（JSON Schema：`{from, to, type, content, metadata}`）还是自由文本？
- **通信模式**：
  - **同步请求-响应**：Agent A 调用 Agent B，等待结果
  - **异步事件驱动**：Agent 发布事件，其他 Agent 订阅
  - **共享黑板（Blackboard）**：所有 Agent 读写共享状态空间
- **上下文传递**：Agent A 把什么上下文传给 Agent B？全量传递会导致 token 爆炸，选择性传递会丢信息

---

## 4. 任务分解与编排

### 4.1 编排模式对比

| 模式 | 适用场景 | 优势 | 风险 |
|------|---------|------|------|
| **DAG 工作流** | 任务结构明确、可静态分解 | 可预测、可并行 | 不适应动态变化 |
| **层级委派** | Manager → Worker 结构 | 职责清晰、可扩展 | Manager 成为瓶颈 |
| **自由对话** | 开放式探索、创意任务 | 灵活、涌现性强 | 难以收敛、不可控 |
| **竞争/投票** | 需要多角度验证 | 提高准确性 | 成本高、可能僵持 |
| **流水线** | 有明确阶段的任务 | 简单、高效 | 阶段间耦合 |

### 4.2 任务分解策略

```
用户请求: "分析腾讯2025年Q2财报并给出投资建议"
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
   静态分解                   动态分解
        │                       │
  ┌─────┼─────┐           Orchestrator
  │     │     │           先调用 Researcher
  ▼     ▼     ▼           → 发现需要数据
数据   分析   报告         → 动态分配给 DataAgent
采集   推理   生成         → 再分配给 AnalysisAgent
```

---

## 5. 上下文窗口与会话管理

这和 Memory 相关但更偏向工程实现：

- **Token 预算分配**：每个 Agent 的上下文窗口如何分配？（系统提示 + 历史 + 当前任务 + 工具结果）
- **上下文压缩**：长对话需要摘要/截断策略
- **会话隔离**：Agent A 的上下文不能"泄漏"到 Agent B，除非显式传递
- **上下文继承 vs 重构**：子任务 Agent 是继承父 Agent 的上下文，还是从零开始？

```python
# 典型的上下文预算分配
context_budget = {
    "system_prompt": 2000,      # 角色定义
    "task_description": 1000,   # 当前任务
    "history_summary": 3000,    # 历史摘要
    "tool_results": 6000,       # 工具返回
    "scratchpad": 2000,         # Agent 思考空间
    "reserved": 12792           # 输出空间
}
# 总计 32K tokens (以 GPT-4 32K 为例)
```

---

## 6. 工具系统与资源管理

- **工具注册与发现**：Agent 如何知道有哪些工具可用？动态发现 vs 静态配置
- **工具权限**：哪些 Agent 能用哪些工具？（如：只有 Coder Agent 能执行代码，Researcher 只能搜索）
- **资源争用**：多个 Agent 同时调用同一工具/API，需要限流和锁机制
- **工具调用冲突**：两个 Agent 同时修改同一文件怎么办？

---

## 7. 错误处理与鲁棒性

这是生产级系统最容易忽视的部分：

| 错误类型 | 应对策略 |
|---------|---------|
| **Agent 输出格式错误** | 结构化输出校验 + 重试机制 |
| **Agent 幻觉** | 交叉验证（多 Agent 投票）、事实核查 Agent |
| **Agent 陷入循环** | 最大轮次限制 + 循环检测 |
| **工具调用失败** | 降级策略 + 备选工具 |
| **Agent "罢工"（拒绝执行）** | 重新分配给其他 Agent |
| **超时** | 每步超时 + 全局超时 |
| **级联失败** | 熔断机制，防止单点故障扩散 |

### Agent 执行循环

```
┌──→ 执行 ──→ 成功? ──→ 完成
│       │
│       ↓ 失败
│     重试(<3次)?
│       │
│       ↓ 否
│     降级/委派其他Agent
│       │
└───────┘
```

---

## 8. 收敛与终止条件

**Multi-Agent 系统最常见的失败模式是不收敛**——Agent 之间无限对话、互相推诿、或者反复修改。

### 8.1 终止条件设计

- 目标达成（任务完成信号）
- 最大轮次 / 最大 token 消耗
- 超时
- 人工干预

### 8.2 冲突解决

两个 Agent 给出矛盾结论时，谁说了算？

- 优先级机制（Reviewer > Coder）
- 投票机制
- 升级到人类

---

## 9. 可观测性（Observability）

调试 Multi-Agent 系统比单 Agent 难一个数量级：

- **全链路追踪**：每个消息的来源、去向、耗时
- **Agent 行为日志**：每个 Agent 的推理过程、工具调用、决策
- **成本追踪**：每个 Agent / 每个任务消耗了多少 token
- **可视化**：Agent 交互图谱、任务执行时间线
- **回放能力**：能重现某次执行的全部细节

### Trace 示例

```
[00:00.000] Orchestrator → 分解任务 → [Researcher, Analyst, Writer]
[00:02.341] Researcher → 调用 web_search("腾讯 Q2 财报") → 3 results
[00:05.122] Researcher → 调用 westock-data finance hk00700 → 财务数据
[00:08.456] Researcher → 返回结果给 Orchestrator (tokens: 3421)
[00:08.789] Analyst → 接收数据 → 开始分析...
[00:15.234] Analyst → 返回分析结论 (tokens: 2103)
[00:15.567] Writer → 接收分析 → 生成报告...
[00:22.891] Writer → 返回报告 (tokens: 1856)
[00:23.012] Orchestrator → 任务完成 ✓  总耗时: 22.9s  总成本: $0.12
```

---

## 10. 安全与治理

- **Agent 间信任边界**：Agent A 能否完全信任 Agent B 的输出？需要验证吗？
- **敏感信息隔离**：某些 Agent 不应看到某些数据（如用户隐私数据）
- **权限分级**：读取 vs 写入 vs 执行（代码执行 Agent 的权限要严格限制）
- **审计日志**：谁在什么时候做了什么，可追溯
- **Human-in-the-Loop (HITL)**：哪些决策需要人类确认？（高风险操作如发邮件、执行交易）

---

## 11. 评估与测试

- **端到端评估**：整个系统的输出质量如何？
- **Agent 级评估**：每个 Agent 单独的表现如何？
- **协作效率评估**：Agent 之间的协作是否高效？有没有冗余？
- **回归测试**：修改一个 Agent 后，整个系统表现是否下降？

---

## 12. 优先级建议

如果要从零开始构建，建议按以下优先级推进：

| 优先级 | 模块 | 理由 |
|--------|------|------|
| **P0** | Agent 角色定义 + 编排器 | 系统骨架，决定了能否运行 |
| **P0** | 通信协议 | Agent 间协作的基础 |
| **P0** | 终止条件 | 防止系统失控 |
| **P1** | 错误处理 + 重试 | 生产级可用性的前提 |
| **P1** | 可观测性 | 没有它几乎无法调试 |
| **P1** | 上下文管理 | 直接影响输出质量 |
| **P2** | Memory 系统 | 提升长期性能，但非首发必需 |
| **P2** | 安全与权限 | 取决于应用场景的风险等级 |
| **P3** | 评估体系 | 持续优化的基础 |
| **P3** | 扩展性优化 | 规模化时再考虑 |

---

## 附录：Memory 系统说明

Memory 系统在上述框架中属于 **P2 优先级**，是提升长期性能的重要组件但非首发必需。其核心职责包括：

- **跨会话上下文保持**：记录历史决策、用户偏好、项目约定
- **分层存储**：短期工作记忆（会话级）+ 长期记忆（持久化）
- **记忆更新策略**：追加式日志 + 可更新的长期事实库
- **冲突处理**：新记忆与旧记忆矛盾时的更新机制
- **遗忘机制**：过期信息的清理与蒸馏

> Memory 系统应与可观测性（Trace/日志）配合，避免"黑箱记忆"导致难以调试的问题。

---

## 13. 软件开发专用架构：SDD + Multi-Agent + Loop 三层融合

> 以下章节是对通用框架的**领域特化**，面向软件开发任务（前后端分离 WEB / PC-QT 客户端 / 微信小程序），目标用户为**个人开发者 + AI Loop**场景。

### 13.1 设计定位

本文档前 1-12 章描述的是**通用 Multi-Agent 框架**，适用于任意领域。但从通用框架到"软件开发工厂"还差四层东西：

| 通用框架覆盖 | 软件开发还需补齐 |
|-------------|----------------|
| Agent 角色抽象 | 软件开发专用角色（前后端分离、API 契约、测试、评审） |
| 任务分解策略 | 需求 → 规约 → 任务卡 → 代码 的软件交付链路 |
| 通信协议 | 前后端 API 契约、共享黑板、代码隔离 |
| 终止条件 | 验收标准（Done 条件）、测试通过率、覆盖率 |
| 工具系统 | Git Worktree、CI/CD、代码审查、测试运行器 |

本节用 **三层融合架构** 补齐这些缺口：

```
┌──────────────────────────────────────────────────┐
│            Layer 1: Spec 规约层                  │
│   (做什么 / 做完的标准 / 边界约束)              │
│   .loop/ 目录 ← 轻量规约，1文件1任务            │
├──────────────────────────────────────────────────┤
│            Layer 2: Loop 驱动层                  │
│   (自动循环 / 预算控制 / 收敛终止)              │
│   Loop Engine ← Goal-Done 循环 + 预算门控        │
├──────────────────────────────────────────────────┤
│            Layer 3: Multi-Agent 执行层          │
│   (谁来做 / 怎么协作 / 隔离与合并)              │
│   agents/ 目录 ← 8 个软件开发专用角色           │
├──────────────────────────────────────────────────┤
│            工程基础设施                          │
│   Git Worktree / 共享黑板 / API 契约 / CI       │
└──────────────────────────────────────────────────┘
```

### 13.2 三层职责边界

| 层 | 职责 | 不做什么 |
|----|------|---------|
| **Spec 规约层** | 定义"做什么"和"做完的标准" | 不关心"怎么做"（实现细节交给 Agent） |
| **Loop 驱动层** | 驱动循环、控制预算、判断收敛 | 不执行具体开发动作 |
| **Multi-Agent 执行层** | 执行开发、测试、评审、部署 | 不改变任务目标（目标由 Spec 锁定） |

**核心原则：Spec 锁定目标，Loop 驱动过程，Agent 执行动作——三者解耦，各司其职。**

### 13.3 软件开发专用 Agent 角色

针对软件开发任务（前后端分离 WEB + PC-QT 客户端），定义 10 个 Agent：

| Agent | 职责 | 输入 | 输出 | 优先级 |
|-------|------|------|------|--------|
| **Orchestrator** | 任务分解、调度、冲突仲裁 | 用户需求 / Spec | 任务卡 + 分配方案 | P0 |
| **SpecAgent** | 需求澄清、验收标准定义 | 用户模糊需求 | `.loop/active/*.md` 任务卡 | P0 |
| **ArchitectAgent** | 技术选型、API 契约定义、目录结构 | 任务卡 | `shared/api-contract.md` + 架构决策 | P1 |
| **UIDesignAgent** | UI 设计规约（Web:页面/组件/tokens；QT:窗口/控件/信号槽/QSS） | 任务卡 + 架构决策 | `ui-design.json` 设计规约（含 platform 字段） | P1 |
| **BackendDevAgent** | 后端实现（API、业务逻辑、数据层） | 任务卡 + API 契约 | 代码 + 单测 | P1 |
| **FrontendDevAgent** | 前端实现（页面、组件、状态管理） | 任务卡 + API 契约 + UI 设计规约 | 代码 + 组件 | P1 |
| **ClientDevAgent** | QT 客户端实现（Qt5+Widget+CMake） | 任务卡 + API 契约 + UI 设计规约（QT 模式） | 代码 diff（.ui/.cpp/.qss/CMakeLists） | P1 |
| **TestAgent** | 测试编写、覆盖率、回归验证 | 代码 + 任务卡 | 测试报告 | P2 |
| **ReviewAgent** | 代码评审、安全审查、规范检查 | PR / 代码变更 | 评审意见 + Approve/Reject | P2 |
| **DevOpsAgent** | 构建、部署、环境管理 | 构建配置 | 部署结果 + 日志 | P3 |

### 13.4 软件交付链路

```
用户需求
  │
  ▼
SpecAgent ──→ 生成 .loop/active/{project}/task-xxx.md（任务卡）
  │              Goal / Done / Constraints
  ▼
Orchestrator ──→ 分解为子任务，分配给 Agent
  │              优先级排序、依赖分析
  ▼
ArchitectAgent ──→ 定义 API 契约（shared/api-contract.md）
  │                  技术选型、目录结构
  ▼
┌─────────────────┬─────────────────┐
│ BackendDevAgent │ UIDesignAgent    │  ← 并行，各自 Worktree
│   实现后端API   │   设计页面/组件  │
│   + 单元测试    │   交互/状态/tokens│
└────────┬────────┴────────┬────────┘
         │                 │
         │                 ▼
         │        FrontendDevAgent  ← UI Review 通过后启动
         │           实现前端页面
         │           + 组件测试
         │                 │
         └────────┬────────┘
         │                 │
         ▼                 ▼
    TestAgent ──→ 集成测试、覆盖率检查
         │
         ▼
    ReviewAgent ──→ 代码评审
         │
    ┌────┴────┐
    ▼         ▼
 Approve   Reject → 回到 DevAgent 修改
    │
    ▼
 DevOpsAgent ──→ 构建 + 部署
    │
    ▼
 任务卡 Done 项全部勾选 → 归档到 .loop/done/
```

### 13.4.1 QT 客户端交付分支（二期）

QT 客户端复用 WEB 后端 API 契约，与 BackendDevAgent 并行启动，依赖 UIDesignAgent 输出 QT 模式设计规约：

```
ArchitectAgent ──→ API 契约（shared/api-contract.md，WEB 与 QT 共用）
    │
    ├─→ BackendDevAgent（后端实现，与 QT 客户端并行）
    │
    └─→ UIDesignAgent（QT 模式 platform:"qt"）──→ ClientDevAgent
                                                 实现窗口/控件/信号槽/QSS
                                                 .ui/.cpp/.qss/CMakeLists.txt
                                                 │
                                                 ▼
                                              TestAgent（Qt Test + ctest）
                                                 │
                                                 ▼
                                              ReviewAgent
```

**关键约定**：
- QT 客户端与 WEB 共享同一份 `shared/api-contract.md`，后端 API 不变
- UIDesignAgent 通过 `platform: "qt"` 输出 QT 设计规约（窗口/控件/信号槽/QSS）
- ClientDevAgent 用 QNetworkAccessManager 消费 API 契约

### 13.5 前后端分离架构的协作桥接

前后端分离的核心挑战是**契约同步**。采用以下机制：

**API 契约即单一事实来源**：
- `shared/api-contract.md` 定义所有 API 的请求/响应 schema
- 后端实现必须符合契约，前端调用必须符合契约
- 契约变更必须经过 ArchitectAgent，并同步通知前后端

**Git Worktree 隔离**：
```
01Loop-WorkSpace/
├── worktrees/
│   ├── backend/     ← BackendDevAgent 工作区
│   ├── frontend/    ← FrontendDevAgent 工作区
│   └── shared/      ← 共享依赖（API类型定义）
```
- 每个 Agent 在独立 Worktree 中工作，避免代码冲突
- 合并通过 ReviewAgent 评审后执行

### 13.6 落地目录结构

```
d:\01Loop-WorkSpace\
├── doc/
│   ├── design.md              ← 本文档（通用框架 + 软件开发专用架构）
│   └── architecture.md        ← 三层融合架构详细设计
├── .loop/                     ← Layer 1: Spec 规约层
│   ├── context.md             ← 项目全局上下文
│   ├── loop.md                ← Loop 配置（预算、终止条件、策略）
│   ├── active/                ← 进行中任务（每个任务一个 .md）
│   └── done/                  ← 已完成任务归档
├── agents/                    ← Layer 3: Agent 定义层
│   ├── README.md              ← Agent 定义规范
│   ├── orchestrator.md        ← 编排器
│   ├── spec-agent.md          ← 规约 Agent
│   ├── architect-agent.md     ← 架构 Agent
│   ├── ui-design-agent.md      ← UI 设计 Agent（Web + QT 双模式）
│   ├── backend-dev.md         ← 后端开发 Agent
│   ├── frontend-dev.md        ← 前端开发 Agent
│   ├── client-dev.md          ← QT 客户端开发 Agent
│   ├── test-agent.md          ← 测试 Agent
│   ├── review-agent.md       ← 评审 Agent
│   └── devops-agent.md        ← DevOps Agent
├── shared/                    ← 共享黑板
│   ├── blackboard.md          ← 共享状态空间
│   └── api-contract.md        ← API 契约（单一事实来源，跨端共享）
├── projects/                  ← 实际项目代码
│   ├── web-app/               ← 前后端分离 WEB 应用
│   │   ├── backend/           ← 后端代码
│   │   └── frontend/          ← 前端代码
│   └── pc-client/             ← QT 客户端应用
│       ├── src/               ← C++/Qt 源码
│       └── CMakeLists.txt     ← CMake 构建
└── worktrees/                 ← Git Worktree 隔离区（按需创建）
```

### 13.7 与通用框架的映射关系

| 通用框架维度（1-12章） | 软件开发落地方式（13章 + architecture.md §8-§9） |
|----------------------|----------------------------------------------|
| Agent 角色定义（§2） | 8 个软件开发专用角色（§13.3）+ 工具化 IO Schema（architecture.md §9） |
| 通信与协调（§3） | 星型拓扑 + 结构化消息 Schema（architecture.md §4.2）+ API 契约（§13.5） |
| 任务分解与编排（§4） | 调度器驱动 Workflow + 嵌入式 LLM 决策点（architecture.md §8.2） |
| 上下文管理（§5） | artifact store 外部化 + 传引用不传内容（architecture.md §8.3, §9.4） |
| 工具系统（§6） | Git Worktree、CI/CD、测试运行器、声明式闸门（architecture.md §8.5） |
| 错误处理（§7） | 三层闸门 + 回滚 checkpoint + 重试策略（architecture.md §8.5-§8.6） |
| 收敛与终止（§8） | 状态机驱动 + Done 项声明式检查（architecture.md §8.4） |
| 可观测性（§9） | `shared/blackboard.md` 结构化 Trace + artifact store |
| 安全与治理（§10） | ReviewAgent 三层质检 + Git 分支保护 + L3 人工抽查 |
| 评估与测试（§11） | Gate-Auto 自动闸门 + TestAgent 覆盖率报告 |

### 13.8 首期实施路线图

| 阶段 | 交付物 | 验收标准 |
|------|--------|---------|
| **Phase 0: 基础设施** | `.loop/` + `agents/` + `shared/` 目录结构 | 目录创建完成，模板就绪 |
| **Phase 1: 规约层** | 任务卡模板 + Loop 配置 | 能将一个用户需求转化为标准任务卡 |
| **Phase 2: 核心Agent** | Orchestrator + SpecAgent + ArchitectAgent | 能完成需求→规约→架构决策链路 |
| **Phase 3: 开发Agent** | BackendDevAgent + FrontendDevAgent | 能基于 API 契约并行开发 |
| **Phase 4: 质量保障** | TestAgent + ReviewAgent | 测试覆盖率 ≥ 80%，评审通过率 ≥ 90% |
| **Phase 5: DevOps** | DevOpsAgent + CI/CD | 自动构建部署流水线就绪 |
| **Phase 6: 运行时架构重构** | 调度器+状态机+工具化Agent+闸门+产物外部化 | 混合模式调度器就绪 |
| **Phase 7: 首个WEB应用** | `projects/web-app/` 完整应用 | 端到端走通一次完整 Loop |

### 13.9 运行时架构要点

> 详细运行时架构设计见 `doc/architecture.md` §8-§9。

本框架的运行时采用**混合模式：确定性 Workflow Engine + 嵌入式 LLM 决策点**：

- **调度器**是确定性代码（状态机驱动），不是 LLM 会话
- **Agent** 按需新开会话实例化，执行完即销毁，LLM 无状态
- **状态管理**在调度器侧（任务卡 frontmatter），不在 LLM 内存
- **上下文管理**在 artifact store（文件系统），交接传引用不传内容
- **质量控制**三层闸门：L1 自动脚本 → L2 LLM 评审 → L3 人工抽查
- **失败恢复**靠 Git checkpoint 回滚 + 重试上限

核心原则：**Agent 不保存主状态、任务状态在调度器、上下文在 artifact store、交接靠结构化产物、质量靠审核节点、失败靠重试策略。**
