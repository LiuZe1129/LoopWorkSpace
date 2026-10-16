# API 契约（单一事实来源）

> 前后端的 API 契约定义。后端实现必须符合此契约，前端调用必须符合此契约。
> 变更必须经 ArchitectAgent 审核，并通知所有相关 Agent。

## 契约规范

- 所有 API 以 `/api` 为前缀
- 请求和响应均为 JSON 格式
- 时间字段统一使用 ISO 8601 格式
- 错误响应统一格式：`{ "error": "string", "code": "string" }`
- 分页参数：`?page=1&limit=20`
- 分页响应：`{ "data": [], "total": number, "page": number, "limit": number }`

## API 端点

> 首期 WEB 应用启动后，由 ArchitectAgent 根据任务卡逐步定义。

### 待定义

（暂无 API 端点，等待第一个任务触发）

---

## 变更历史

| 日期 | 变更内容 | 变更人（Agent） | 影响范围 |
|------|---------|----------------|---------|
| - | - | - | - |
