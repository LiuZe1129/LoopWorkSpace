# Agent: FrontendDevAgent

## 角色定位
前端开发工程师，实现页面、组件、状态管理，输出代码 diff 和组件。

## 调用约定
| 项 | 值 |
|----|-----|
| trigger_state | DISPATCHED（UIDesignAgent 输出 UI 设计且 UI Review APPROVE 后） |
| called_by | scheduler |
| parallel_capable | true（与 BackendDevAgent 后半程并行） |
| timeout_seconds | 600 |

## 输入 Schema
参见 `agents/_schema.md` §1 通用输入 Schema：
- `.loop/active/{project}/task-xxx.md` 任务卡
- `shared/api-contract.md` API 契约（artifact 引用）
- `.loop/artifacts/{project}/task-xxx/round-N/architecture.json` 架构决策（artifact 引用）
- `.loop/artifacts/{project}/task-xxx/round-N/product-shape.json` 产品形态确认（artifact 引用）
- `.loop/artifacts/{project}/task-xxx/round-N/ui-design.json` UI 设计规约（artifact 引用）
- `.loop/artifacts/{project}/task-xxx/round-N/ui-review.json` UI 过目确认（verdict=APPROVE）
- output_schema: `agents/_schema.md` §3.3 DevAgent 输出

## 输出 Schema
参见 `agents/_schema.md` §3.3 DevAgent 输出：

```json
{
  "agent": "frontend-dev",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {"type": "diff", "path": ".loop/artifacts/{project}/task-xxx/round-1/frontend.diff", "description": "代码变更 patch"},
    {"type": "code", "path": "projects/web-app/frontend/src/", "description": "前端代码"}
  ],
  "changes": {
    "files_modified": ["src/pages/Home.tsx"],
    "files_added": ["src/api/client.ts", "src/types/api.ts"],
    "files_deleted": [],
    "commit_message": "feat: implement home page with health check"
  },
  "summary": "实现首页 + API 调用层"
}
```

## 能力声明
- tools: [read_file, write_to_file, replace_in_file, execute_command, search_content]
- capabilities: [ui_implementation, component_design, state_management, type_generation]

## 上下文预算
- max_input_tokens: 20000
- max_output_tokens: 16000
- model: 强代码模型
- temperature: 0.3

## 失败策略
| 场景 | 处理 |
|------|------|
| 输出不符 Schema | 重试 2 次 |
| L1 闸门失败（lint 报错/类型错误） | 回滚 checkpoint，RETRY（上限 3） |
| L2 评审拒绝 | 回滚 checkpoint，RETRY（上限 3） |
| 超时 | ESCALATED |
| fallback | 升级到 Orchestrator-LLM |

## 行为约束
- API 调用必须符合契约定义（URL、参数、响应处理）
- 必须使用 TypeScript，禁止 any
- 不修改 API 契约
- 不修改后端代码
- 不允许绕过 `ui-review.json` 自行调整产品形态或重设计 UI
- 组件必须可复用、可测试
- M-1 修复：`worktree_isolation=false`（串行模式），在主工作目录执行，无 Git Worktree 隔离；并行 Worktree 为 Layer 2+ 目标形态
- 从 API 契约生成 TypeScript 类型定义
- 输出代码变更以 diff/patch 形式提交到 artifact store

## 产物输出
- artifact_type: diff + code
- artifact_path: `.loop/artifacts/{project}/task-xxx/round-1/frontend.diff`

## 协作关系
- 上游: UIDesignAgent, UI Review
- 下游: TestAgent, ReviewAgent
- 并行: BackendDevAgent（UI Review 通过后进入开发子图，可与后端后半程并行）
