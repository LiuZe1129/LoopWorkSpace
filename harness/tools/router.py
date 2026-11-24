"""Agent-Harness Tool Router。

该模块是所有 ToolCall 的唯一入口，依次执行 Registry 查找、Schema 校验、Policy、
幂等 Ledger、Sandbox Tool 和 Event 记录；Agent 不持有执行器或 SandboxRuntime。
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from harness.contracts import Task
from harness.errors import HarnessError
from harness.integration import AgentProfile
from harness.persistence import EventStore
from harness.sandbox import SandboxManager

from .basic import BasicToolExecutor
from .contracts import PolicyAction, PolicyDecision, ToolCall, ToolResult, ToolStatus
from .ledger import LedgerStatus, ToolLedger
from .policy import PolicyContext, PolicyEngine
from .registry import ToolRegistry


class ToolRouter:
    """编排受控 ToolCall，位于 Harness Agent Loop 与 Sandbox 的唯一执行边界。"""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyEngine,
        ledger: ToolLedger,
        sandboxes: SandboxManager,
        *,
        platform_tools: frozenset[str],
        event_store: EventStore | None = None,
        approval_required_tools: frozenset[str] = frozenset(),
        approved_tools: frozenset[str] = frozenset(),
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._ledger = ledger
        self._sandboxes = sandboxes
        self._executor = BasicToolExecutor(sandboxes)
        self._platform_tools = platform_tools
        self._events = event_store
        self._approval_required_tools = approval_required_tools
        self._approved_tools = approved_tools

    def route(
        self,
        task: Task,
        profile: AgentProfile,
        *,
        execution_id: str,
        attempt_id: str,
        sandbox_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        idempotency_key: str,
    ) -> ToolResult:
        """执行完整 Tool 管线，位于 Harness Agent 产生 ToolCall 后的唯一处理环节。"""
        call = ToolCall(
            call_id=f"call-{uuid4().hex}",
            execution_id=execution_id,
            attempt_id=attempt_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            idempotency_key=idempotency_key,
        )
        if task.agent_profile != profile.to_ref():
            return self._deny(call, "agent_profile_mismatch", "Task 绑定的 AgentProfile 与运行时 Profile 不一致")
        definition = self._registry.get(tool_name)
        if definition is None:
            return self._deny(call, "unknown_tool", "工具未注册")
        validation_errors = self._registry.validate_arguments(definition, call.arguments)
        if not idempotency_key:
            validation_errors.append("$.idempotency_key: required")
        if validation_errors:
            return self._deny(call, "invalid_arguments", "; ".join(validation_errors))
        sandbox_available = self._sandboxes.owns(sandbox_id, execution_id, attempt_id)
        decision = self._policy.evaluate(
            call,
            definition,
            PolicyContext(
                task_tools=frozenset(task.permissions.tools),
                agent_tools=frozenset(profile.tools),
                platform_tools=self._platform_tools,
                sandbox_available=sandbox_available,
                approved_tools=self._approved_tools,
                approval_required_tools=self._approval_required_tools,
            ),
        )
        if decision.action != PolicyAction.ALLOW:
            result = ToolResult(
                call.call_id,
                call.tool_name,
                ToolStatus.DENIED,
                decision,
                error_code=decision.code,
                error_message=decision.reason,
            )
            self._record(call, result)
            return result
        existing = self._ledger.inspect(call)
        recovered = self._recover_completed_write(call, decision, sandbox_id, existing)
        if recovered is not None:
            self._ledger.complete(call, recovered)
            self._record(call, recovered, replayed=True)
            return recovered
        record = self._ledger.begin(call, safe_to_replay=not definition.side_effecting)
        if record.status == LedgerStatus.COMPLETED and record.result is not None:
            self._record(call, record.result, replayed=True)
            return record.result
        if record.status == LedgerStatus.AMBIGUOUS:
            result = ToolResult(
                call.call_id,
                call.tool_name,
                ToolStatus.AMBIGUOUS,
                decision,
                error_code="side_effect_ambiguous",
                error_message="上一次执行结果不确定，禁止自动重放",
            )
            self._record(call, result)
            return result
        try:
            result = self._executor.execute(call, definition, decision, sandbox_id)
        except (HarnessError, OSError, ValueError) as exc:
            if definition.side_effecting:
                self._ledger.mark_ambiguous(call)
            else:
                self._ledger.abandon(call)
            result = ToolResult(
                call.call_id,
                call.tool_name,
                ToolStatus.AMBIGUOUS if definition.side_effecting else ToolStatus.FAILED,
                decision,
                error_code="tool_infrastructure_error",
                error_message=str(exc),
            )
            self._record(call, result)
            return result
        self._ledger.complete(call, result)
        self._record(call, result)
        return result

    def _recover_completed_write(
        self,
        call: ToolCall,
        decision: PolicyDecision,
        sandbox_id: str,
        existing,
    ) -> ToolResult | None:
        if (
            call.tool_name != "write_file"
            or existing is None
            or existing.status != LedgerStatus.STARTED
        ):
            return None
        relative = PurePosixPath(str(call.arguments["path"]))
        workspace = self._sandboxes.handle(sandbox_id).workspace.resolve()
        target = workspace.joinpath(*relative.parts)
        try:
            resolved = target.resolve()
            resolved.relative_to(workspace)
        except (OSError, ValueError):
            return None
        if not resolved.is_file() or target.is_symlink():
            return None
        expected = str(call.arguments["content"]).encode("utf-8")
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        if digest.digest() != hashlib.sha256(expected).digest():
            return None
        return ToolResult(
            call.call_id,
            call.tool_name,
            ToolStatus.SUCCEEDED,
            decision,
            output={
                "path": relative.as_posix(),
                "bytes": len(expected),
                "sha256": digest.hexdigest(),
            },
            metadata={"recovered_from_workspace_hash": True},
        )

    def _deny(self, call: ToolCall, code: str, reason: str) -> ToolResult:
        decision = PolicyDecision(
            f"policy-{uuid4().hex}",
            PolicyAction.DENY,
            code,
            reason,
        )
        result = ToolResult(
            call.call_id,
            call.tool_name,
            ToolStatus.DENIED,
            decision,
            error_code=code,
            error_message=reason,
        )
        self._record(call, result)
        return result

    def _record(self, call: ToolCall, result: ToolResult, *, replayed: bool = False) -> None:
        if self._events is None:
            return
        self._events.append(
            call.execution_id,
            "tool.call",
            attempt_id=call.attempt_id,
            payload={
                "call_id": result.call_id,
                "tool_name": call.tool_name,
                "status": result.status.value,
                "policy": result.policy.to_dict(),
                "error_code": result.error_code,
                "replayed": replayed,
            },
        )
