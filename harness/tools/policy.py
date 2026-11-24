"""Agent-Harness Tool Policy Engine。

该模块位于 Schema 校验与 Sandbox 执行之间，计算 Task、AgentProfile 与平台允许工具的
交集，并对高风险工具执行审批策略，任何一层未授权都必须拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .contracts import PolicyAction, PolicyDecision, ToolCall, ToolDefinition


@dataclass(frozen=True)
class PolicyContext:
    """封装授权输入，位于 Harness Task/Agent/Platform 权限交集计算边界。"""

    task_tools: frozenset[str]
    agent_tools: frozenset[str]
    platform_tools: frozenset[str]
    sandbox_available: bool
    approved_tools: frozenset[str] = frozenset()
    approval_required_tools: frozenset[str] = frozenset()


class PolicyEngine:
    """计算 ToolCall 授权结论，位于 Harness Router 执行任何副作用之前。"""

    def evaluate(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        context: PolicyContext,
    ) -> PolicyDecision:
        """返回 allow/deny/approval，位于 Harness Tool 执行流程的强制策略门禁。"""
        decision_id = f"policy-{uuid4().hex}"
        if call.tool_name not in context.task_tools:
            return PolicyDecision(decision_id, PolicyAction.DENY, "task_not_authorized", "Task 未授权该工具")
        if call.tool_name not in context.agent_tools:
            return PolicyDecision(decision_id, PolicyAction.DENY, "agent_not_authorized", "AgentProfile 未声明该工具")
        if call.tool_name not in context.platform_tools:
            return PolicyDecision(decision_id, PolicyAction.DENY, "platform_not_authorized", "平台策略未开放该工具")
        if not context.sandbox_available:
            return PolicyDecision(decision_id, PolicyAction.DENY, "sandbox_unavailable", "Sandbox 不可用，禁止回退宿主执行")
        if call.tool_name in context.approval_required_tools and call.tool_name not in context.approved_tools:
            return PolicyDecision(decision_id, PolicyAction.REQUIRE_APPROVAL, "approval_required", "高风险工具需要审批")
        return PolicyDecision(decision_id, PolicyAction.ALLOW, "allowed", "Task、Agent 与平台权限交集允许执行")
