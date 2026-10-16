# Agent: UIDesignAgent

## 角色定位
UI 设计师，将需求 + 架构决策转化为结构化 UI 设计规约（页面/组件/状态/交互/设计 tokens），供 FrontendDevAgent 消费。

## 调用约定
| 项 | 值 |
|----|-----|
| trigger_state | DISPATCHED（ArchitectAgent 输出架构后） |
| called_by | scheduler |
| parallel_capable | true（与 BackendDevAgent 并行） |
| timeout_seconds | 600 |

## 输入 Schema
参见 `agents/_schema.md` §1 通用输入 Schema：
- `.loop/active/{project}/task-xxx.md` 任务卡
- `.loop/context.md` 项目上下文
- `shared/api-contract.md` API 契约（artifact 引用）
- `.loop/artifacts/{project}/task-xxx/round-N/architecture.json` 架构决策（artifact 引用）
- `.loop/artifacts/{project}/task-xxx/round-N/product-shape.json` 产品形态确认（artifact 引用）
- **`dahua-direction/UI/DESIGN.md`** — huaDesign 设计系统语言文档。生成 UI 前必读 §1 视觉基调、§9.1 设计基调 Prompt、§9.2 页面类型→组件矩阵、§9.3 自检清单、§9.4 禁止事项。
- **`dahua-direction/UI/tokens/hua_design_tokens.json`** — huaDesign 跨平台单一数据源 token JSON。`design_tokens` / `qss_tokens` 中所有色值、间距、圆角、阴影、字号必须以 dot-path 引用本文件路径（如 `"color.primary.6"`、`"spacing.x4"`、`"radius.default"`），禁止裸 hex / 裸 px 值。
- output_schema: `agents/_schema.md` §3.8 UIDesignAgent 输出

## 输出 Schema
参见 `agents/_schema.md` §3.8 UIDesignAgent 输出。设计规约通过 `platform` 字段区分 Web / QT 模式：

### Web 模式（platform: "web"）

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
    "platform": "web",
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

### QT 模式（platform: "qt"）

```json
{
  "agent": "ui-design-agent",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {"type": "design", "path": ".loop/artifacts/{project}/task-xxx/round-1/ui-design.json", "description": "QT UI 设计规约"}
  ],
  "ui_design": {
    "platform": "qt",
    "windows": [
      {
        "name": "MainWindow",
        "class": "QMainWindow",
        "layout": "QVBoxLayout（menubar + toolbar + central + statusbar）",
        "widgets": ["QMenuBar", "QToolBar", "QTableView", "QStatusBar"],
        "states": ["loading", "error", "empty", "success"]
      },
      {
        "name": "LoginDialog",
        "class": "QDialog",
        "layout": "QFormLayout",
        "widgets": ["QLineEdit(user)", "QLineEdit(pwd, EchoMode=Password)", "QCheckBox(remember)", "QDialogButtonBox"],
        "states": ["idle", "submitting", "error"]
      }
    ],
    "widget_inventory": [
      {
        "name": "UserTable",
        "base_class": "QTableView",
        "type": "reusable",
        "properties": {"model": "QStandardItemModel", "selectionBehavior": "SelectRows"},
        "signals": ["doubleClicked(QModelIndex)"]
      }
    ],
    "signals_slots": [
      {"signal": "LoginDialog::accepted", "slot": "MainWindow::onLoginSuccess", "description": "登录成功加载主界面"},
      {"signal": "UserTable::doubleClicked", "slot": "MainWindow::openDetail", "description": "双击行打开详情"}
    ],
    "qss_tokens": {
      "design_system": "huaDesign",
      "tokens_ref": "dahua-direction/UI/tokens/hua_design_tokens.json",
      "colors": {
        "primary": "color.primary.6",
        "window_background": "color.neutral.bg",
        "card_background": "color.neutral.bgCard",
        "text_primary": "color.neutral.fontR",
        "border": "color.neutral.split"
      },
      "fonts": {"default": "Microsoft YaHei 10pt", "heading": "Microsoft YaHei 14pt bold"},
      "spacing": {"margin": "spacing.x4", "padding": "spacing.x3"}
    },
    "accessibility": ["Tab 键焦点遍历", "快捷键", "高对比度 QSS"]
  },
  "summary": "定义 N 个窗口、N 个控件、信号槽连接、QSS 规范"
}
```

## 能力声明
- tools: [read_file, write_to_file, replace_in_file, search_content]
- capabilities: [page_design, component_inventory, interaction_design, design_tokens, accessibility_spec, qt_window_design, widget_inventory, signal_slot_design, qss_tokens]

## 上下文预算
- max_input_tokens: 15000
- max_output_tokens: 12000
- model: 强推理模型
- temperature: 0.5

## 失败策略
| 场景 | 处理 |
|------|------|
| 输出不符 Schema | 重试 2 次 |
| L1 闸门失败（设计规约结构校验） | 回滚 checkpoint，RETRY（上限 3） |
| L2 评审拒绝 | 回滚 checkpoint，RETRY（上限 3） |
| 超时 | ESCALATED |
| fallback | 升级到 Orchestrator-LLM |

## 行为约束
- 设计规约必须是**结构化 JSON**，不生成像素级视觉稿
- 不允许绕过 `product-shape.json` 自行假设产品形态；存在 open_questions 时必须请求 Human Gate
- UI 设计完成后必须等待 `ui-review.json` APPROVE，禁止让 DevAgent 直接边实现边重设计
- 必须通过 `platform` 字段声明目标平台（`web` 或 `qt`）
- Web 模式：页面设计必须覆盖全部需求中的用户场景
- Web 模式：每个页面必须定义四种状态：loading / error / empty / success
- 组件清单必须区分 reusable（复用）和 page-specific（页面专用）
- design_tokens / qss_tokens 必须声明 `design_system: "huaDesign"` + `tokens_ref` 指向 `dahua-direction/UI/tokens/hua_design_tokens.json`，所有色值/间距/圆角/阴影/字号以 dot-path 引用 token（如 `"color.primary.6"`、`"spacing.x4"`），**禁止裸 hex / 裸 px 值**。确定性层 `scheduler/schema.py:validate_ui_design_contract` 强制校验
- **单一主色**：`colors.primary` 必须为 `color.primary.6`（#1C79F4），禁止引入第二主色或 Tailwind 裸值（#3B82F6 等）
- **4n 网格**：spacing 必须引用 `spacing.x1/x2/x3/x4/x6/x10/x16`（4/8/12/16/24/40/64），禁止 5/7/10/13 等非 4n 数值
- **数值 tabular-nums**：数值字段（表格/金额/计数/指标卡）必须启用 `font-variant-numeric: tabular-nums`
- **危险操作二次确认**：error 色 + 删除/批量操作必须配二次确认弹窗
- 遵循 `dahua-direction/UI/DESIGN.md` §9.3 自检清单与 §9.4 禁止事项（生成 UI 后必跑自检：无裸值/4n/单一 primary/tabular-nums/危险操作二次确认/空状态有引导/弹窗有遮罩+阴影 level3/5 端一致）
- 页面组件选择遵循 `DESIGN.md §9.2` 页面类型→组件矩阵
- 可访问性（a11y）必须声明：键盘导航、ARIA、色彩对比度
- 不编写具体代码（代码由 FrontendDevAgent / ClientDevAgent 实现）
- 不修改 API 契约
- 设计规约变更触发 L3 人工抽查（UI 设计具主观性）

### QT 模式额外约束（platform: "qt"）
- 输出 windows / widget_inventory / signals_slots / qss_tokens，不输出 pages
- 每个窗口必须定义基类（QMainWindow / QDialog / QWidget）和布局（QVBoxLayout / QFormLayout 等）
- 每个窗口必须定义状态（至少 idle / submitting / error，或 loading / error / empty / success）
- signals_slots 必须列出关键信号槽连接（signal → slot）
- qss_tokens 必须定义色彩、字体、间距（QSS 可直接消费）

## 产物输出
- artifact_type: design (json)
- artifact_path: `.loop/artifacts/{project}/task-xxx/round-1/ui-design.json`
- requires_before_implementation: `.loop/artifacts/{project}/task-xxx/round-1/ui-review.json` verdict=APPROVE

## 协作关系
- 上游: ArchitectAgent, ProductShape/Human Gate
- 下游: UI Review → FrontendDevAgent / ClientDevAgent
- 并行: BackendDevAgent（仅在 UI Review 通过后与客户端/前端实现进入同一开发子图）
