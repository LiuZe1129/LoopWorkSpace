"""Agent-Harness 外部定义集成层。

该包位于现有 GraphFlow/Markdown 资产与 Harness 稳定领域对象之间，当前阶段负责把旧
Agent Markdown 转换为带版本和哈希的 AgentProfile，后续承载 GraphFlow Handler。
"""

from .agent_profile import AgentProfile, AgentProfileStore, build_agent_profile

__all__ = ["AgentProfile", "AgentProfileStore", "build_agent_profile"]
