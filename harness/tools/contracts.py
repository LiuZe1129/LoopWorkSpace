"""Agent-Harness Tool 层稳定契约。

该模块位于不可信模型输出与 Tool Router 之间，定义工具元数据、调用、PolicyDecision
和结果的稳定结构，使 Agent、Policy、Ledger 与 Sandbox 不通过临时字典耦合。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from harness.contracts import utc_now


class ToolRisk(str, Enum):
    """声明工具风险等级，位于 Harness Policy 决策和审批流程。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolStatus(str, Enum):
    """声明工具结果状态，位于 Harness Router 返回 Agent Observation 的流程。"""

    SUCCEEDED = "succeeded"
    DENIED = "denied"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    AMBIGUOUS = "ambiguous"


class PolicyAction(str, Enum):
    """声明策略动作，位于 Harness Tool Schema 校验后、执行前的授权边界。"""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class ToolDefinition:
    """描述版本化工具能力，位于 Harness Registry 的声明边界。"""

    name: str
    version: str
    input_schema: dict[str, Any]
    risk: ToolRisk
    timeout_seconds: int
    side_effecting: bool
    idempotent: bool
    max_output_bytes: int = 1_048_576

    def to_dict(self) -> dict[str, Any]:
        """序列化工具定义，供 Harness AgentProfile 和审计流程引用。"""
        return {
            "name": self.name,
            "version": self.version,
            "input_schema": self.input_schema,
            "risk": self.risk.value,
            "timeout_seconds": self.timeout_seconds,
            "side_effecting": self.side_effecting,
            "idempotent": self.idempotent,
            "max_output_bytes": self.max_output_bytes,
        }


@dataclass(frozen=True)
class ToolCall:
    """描述一次工具调用，位于 Harness Agent 输出进入 Router 的流程入口。"""

    call_id: str
    execution_id: str
    attempt_id: str
    tool_name: str
    arguments: dict[str, Any]
    idempotency_key: str
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """序列化调用，供 Harness Ledger 和 Tool 事件关联稳定 call_id。"""
        return {
            "call_id": self.call_id,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class PolicyDecision:
    """记录一次工具授权结论，位于 Harness Policy 与 Router 执行之间的强制边界。"""

    decision_id: str
    action: PolicyAction
    code: str
    reason: str
    evaluated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, str]:
        """序列化策略结论，供 Harness ToolResult、Event 和审计流程持久化。"""
        return {
            "decision_id": self.decision_id,
            "action": self.action.value,
            "code": self.code,
            "reason": self.reason,
            "evaluated_at": self.evaluated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PolicyDecision":
        """恢复策略结论，位于 Harness Ledger 重放已完成 ToolResult 的环节。"""
        return cls(
            decision_id=str(payload["decision_id"]),
            action=PolicyAction(str(payload["action"])),
            code=str(payload["code"]),
            reason=str(payload["reason"]),
            evaluated_at=str(payload["evaluated_at"]),
        )


@dataclass(frozen=True)
class ToolResult:
    """描述稳定工具结果，位于 Harness Router 返回 Agent Observation 的交接边界。"""

    call_id: str
    tool_name: str
    status: ToolStatus
    policy: PolicyDecision
    output: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now)
    finished_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """序列化结果，供 Harness Ledger、Checkpoint 和 Agent Observation 持久化。"""
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "policy": self.policy.to_dict(),
            "output": self.output,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "metadata": self.metadata,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ToolResult":
        """恢复工具结果，位于 Harness 幂等键命中后避免重复副作用的返回环节。"""
        return cls(
            call_id=str(payload["call_id"]),
            tool_name=str(payload["tool_name"]),
            status=ToolStatus(str(payload["status"])),
            policy=PolicyDecision.from_dict(payload["policy"]),
            output=payload.get("output"),
            error_code=payload.get("error_code"),
            error_message=payload.get("error_message"),
            metadata=dict(payload.get("metadata", {}) or {}),
            started_at=str(payload["started_at"]),
            finished_at=str(payload["finished_at"]),
        )
