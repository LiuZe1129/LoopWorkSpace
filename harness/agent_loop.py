"""Agent-Harness 单 Agent 多轮执行循环。

该模块位于 Controller、Model Gateway 与 Tool Router 之间，驱动 Reason/Act/Observe，
在模型响应和 ToolResult 后提交 Checkpoint，并在预算、取消或副作用不确定时确定停止。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

from .budget import BudgetMeter
from .cancellation import CancellationToken
from .context_manager import ContextManager
from .contracts import Task
from .controller import HarnessController
from .errors import (
    BudgetExceededError,
    CancellationRequestedError,
    ContextBudgetError,
    HarnessError,
    PauseRequestedError,
)
from .integration import AgentProfile
from .models import ModelError, ModelGateway, ModelMessage
from .persistence import Checkpoint, ExecutionLease
from .tools import ToolRouter, ToolStatus


AGENT_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["action"],
    "properties": {
        "action": {"type": "string", "enum": ["tool_call", "complete"]},
        "tool_name": {"type": "string", "minLength": 1},
        "arguments": {"type": "object"},
        "idempotency_key": {"type": "string", "minLength": 1},
        "result": {"type": "object"},
    },
    "additionalProperties": False,
}


class AgentLoopStatus(str, Enum):
    """描述 Agent Loop 停止原因，位于 Harness Controller 终态映射之前。"""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    TIMED_OUT = "timed_out"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NEEDS_RECONCILIATION = "needs_reconciliation"


@dataclass(frozen=True)
class AgentLoopOutcome:
    """封装 Agent Loop 结果，位于 Harness 循环与 Controller/Validator 的交接边界。"""

    status: AgentLoopStatus
    completion: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


class AgentLoop:
    """运行单 Task 单 Agent 循环，位于 Harness MVP 的多轮执行核心。"""

    def __init__(
        self,
        controller: HarnessController,
        model_gateway: ModelGateway,
        tool_router: ToolRouter,
        *,
        context_manager: ContextManager | None = None,
        budget_meter: BudgetMeter | None = None,
    ) -> None:
        self._controller = controller
        self._models = model_gateway
        self._tools = tool_router
        self._contexts = context_manager or ContextManager()
        self._budget = budget_meter or BudgetMeter()

    def run(
        self,
        task: Task,
        profile: AgentProfile,
        *,
        execution_id: str,
        attempt_id: str,
        sandbox_id: str,
        lease: ExecutionLease,
        cancellation: CancellationToken | None = None,
        resume_from: Checkpoint | None = None,
    ) -> AgentLoopOutcome:
        """执行或恢复多轮循环，位于 Harness Attempt RUNNING 到完成提案的流程环节。"""
        token = cancellation or CancellationToken()
        messages, pending_decision = self._restore_state(resume_from)
        while True:
            try:
                self._check_control(execution_id, token)
                attempt = self._controller.task_store.load_attempt(execution_id, attempt_id)
                if pending_decision is None:
                    self._budget.check(task, attempt, next_rounds=1)
                    window = self._contexts.build(
                        task,
                        profile,
                        history=tuple(messages),
                        model_tokens_used=attempt.model_tokens_used,
                    )
                    request = self._contexts.build_request(
                        window,
                        profile,
                        request_id=f"model-{uuid4().hex}",
                    )
                    model_result = self._models.invoke(
                        request,
                        output_schema=AGENT_DECISION_SCHEMA,
                        cancellation=token,
                    )
                    attempt = self._controller.record_model_usage(
                        execution_id,
                        attempt_id,
                        lease,
                        model_result.total_usage,
                    )
                    self._budget.check(task, attempt)
                    self._check_cancel(execution_id, token)
                    pending_decision = dict(model_result.structured_output)
                    messages.append(
                        ModelMessage(
                            "assistant",
                            json.dumps(pending_decision, ensure_ascii=False, sort_keys=True),
                        )
                    )
                    self._checkpoint(
                        execution_id,
                        attempt_id,
                        lease,
                        "model_response",
                        messages,
                        pending_decision,
                    )
                    self._check_pause(execution_id)
                outcome = self._handle_decision(
                    task,
                    profile,
                    execution_id=execution_id,
                    attempt_id=attempt_id,
                    sandbox_id=sandbox_id,
                    lease=lease,
                    messages=messages,
                    decision=pending_decision,
                    cancellation=token,
                )
                if outcome is not None:
                    return outcome
                pending_decision = None
            except CancellationRequestedError as exc:
                return AgentLoopOutcome(AgentLoopStatus.CANCELLED, error_code="cancelled", error_message=str(exc))
            except PauseRequestedError as exc:
                return AgentLoopOutcome(AgentLoopStatus.PAUSED, error_code="paused", error_message=str(exc))
            except BudgetExceededError as exc:
                status = (
                    AgentLoopStatus.TIMED_OUT
                    if exc.dimension == "wall_seconds"
                    else AgentLoopStatus.BUDGET_EXHAUSTED
                )
                return AgentLoopOutcome(status, error_code=exc.dimension, error_message=str(exc))
            except ContextBudgetError as exc:
                return AgentLoopOutcome(
                    AgentLoopStatus.BUDGET_EXHAUSTED,
                    error_code="context_budget",
                    error_message=str(exc),
                )
            except ModelError as exc:
                # 失败和 Schema 重试同样产生真实用量，必须在退出 Loop 前归属当前 Attempt。
                self._controller.record_model_usage(
                    execution_id,
                    attempt_id,
                    lease,
                    exc.usage,
                )
                return AgentLoopOutcome(
                    AgentLoopStatus.FAILED,
                    error_code=exc.code,
                    error_message=str(exc),
                )
            except HarnessError as exc:
                return AgentLoopOutcome(
                    AgentLoopStatus.FAILED,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )

    def _handle_decision(
        self,
        task: Task,
        profile: AgentProfile,
        *,
        execution_id: str,
        attempt_id: str,
        sandbox_id: str,
        lease: ExecutionLease,
        messages: list[ModelMessage],
        decision: dict[str, Any],
        cancellation: CancellationToken,
    ) -> AgentLoopOutcome | None:
        action = decision.get("action")
        if action == "complete":
            result = decision.get("result")
            if not isinstance(result, dict):
                return AgentLoopOutcome(
                    AgentLoopStatus.FAILED,
                    error_code="invalid_agent_decision",
                    error_message="complete decision requires an object result",
                )
            self._checkpoint(
                execution_id,
                attempt_id,
                lease,
                "completion_proposed",
                messages,
                None,
            )
            return AgentLoopOutcome(AgentLoopStatus.COMPLETED, completion=result)
        if action != "tool_call":
            return AgentLoopOutcome(
                AgentLoopStatus.FAILED,
                error_code="invalid_agent_decision",
                error_message="unknown Agent action",
            )
        tool_name = decision.get("tool_name")
        arguments = decision.get("arguments")
        idempotency_key = decision.get("idempotency_key")
        if not isinstance(tool_name, str) or not isinstance(arguments, dict) or not isinstance(idempotency_key, str):
            return AgentLoopOutcome(
                AgentLoopStatus.FAILED,
                error_code="invalid_agent_decision",
                error_message="tool_call requires tool_name, arguments and idempotency_key",
            )
        attempt = self._controller.task_store.load_attempt(execution_id, attempt_id)
        self._budget.check(task, attempt, next_tool_calls=1)
        result = self._tools.route(
            task,
            profile,
            execution_id=execution_id,
            attempt_id=attempt_id,
            sandbox_id=sandbox_id,
            tool_name=tool_name,
            arguments=arguments,
            idempotency_key=idempotency_key,
        )
        attempt = self._controller.record_tool_usage(execution_id, attempt_id, lease)
        self._budget.check(task, attempt)
        self._check_cancel(execution_id, cancellation)
        messages.append(
            ModelMessage(
                "user",
                "Tool Observation:\n" + json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True),
            )
        )
        self._checkpoint(
            execution_id,
            attempt_id,
            lease,
            "tool_result",
            messages,
            None,
            refs={"tool_call_id": result.call_id},
        )
        self._check_pause(execution_id)
        if result.status == ToolStatus.AMBIGUOUS:
            return AgentLoopOutcome(
                AgentLoopStatus.NEEDS_RECONCILIATION,
                error_code=result.error_code,
                error_message=result.error_message,
            )
        if result.status == ToolStatus.TIMED_OUT:
            return AgentLoopOutcome(
                AgentLoopStatus.TIMED_OUT,
                error_code=result.error_code,
                error_message=result.error_message,
            )
        if result.error_code == "output_limit_exceeded":
            return AgentLoopOutcome(
                AgentLoopStatus.FAILED,
                error_code=result.error_code,
                error_message=result.error_message,
            )
        return None

    def _checkpoint(
        self,
        execution_id: str,
        attempt_id: str,
        lease: ExecutionLease,
        stage: str,
        messages: list[ModelMessage],
        pending_decision: dict[str, Any] | None,
        refs: dict[str, str] | None = None,
    ) -> None:
        self._controller.write_checkpoint(
            execution_id,
            attempt_id,
            lease,
            stage=stage,
            state={
                "messages": [message.to_dict() for message in messages],
                "pending_decision": pending_decision,
            },
            refs=refs,
        )

    def _restore_state(self, checkpoint: Checkpoint | None) -> tuple[list[ModelMessage], dict[str, Any] | None]:
        if checkpoint is None:
            return [], None
        messages = [
            ModelMessage(str(item["role"]), str(item["content"]))
            for item in checkpoint.state.get("messages", [])
        ]
        pending = checkpoint.state.get("pending_decision")
        return messages, None if pending is None else dict(pending)

    def _check_control(self, execution_id: str, token: CancellationToken) -> None:
        self._check_cancel(execution_id, token)
        self._check_pause(execution_id)

    def _check_cancel(self, execution_id: str, token: CancellationToken) -> None:
        token.raise_if_cancelled()
        if self._controller.is_cancel_requested(execution_id):
            token.cancel("persistent cancellation requested")
            token.raise_if_cancelled()

    def _check_pause(self, execution_id: str) -> None:
        if self._controller.is_pause_requested(execution_id):
            raise PauseRequestedError("persistent pause requested")
