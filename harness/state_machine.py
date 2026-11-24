"""Agent-Harness TaskExecution 与 Attempt 状态机。

该模块位于 Harness Controller 修改持久化状态之前，集中拒绝非法跳转，确保模型、
Worker 或恢复流程不能绕过生命周期约束直接写入终态。
"""

from __future__ import annotations

from dataclasses import replace

from .contracts import Attempt, AttemptStatus, TaskExecution, TaskExecutionStatus, utc_now
from .errors import InvalidTransitionError


EXECUTION_TERMINAL_STATUSES = {
    TaskExecutionStatus.SUCCEEDED,
    TaskExecutionStatus.FAILED,
    TaskExecutionStatus.CANCELLED,
    TaskExecutionStatus.TIMED_OUT,
}

ATTEMPT_TERMINAL_STATUSES = {
    AttemptStatus.SUCCEEDED,
    AttemptStatus.FAILED,
    AttemptStatus.CANCELLED,
    AttemptStatus.TIMED_OUT,
    AttemptStatus.NEEDS_RECONCILIATION,
}

EXECUTION_TRANSITIONS: dict[TaskExecutionStatus, set[TaskExecutionStatus]] = {
    TaskExecutionStatus.CREATED: {TaskExecutionStatus.VALIDATING, TaskExecutionStatus.CANCELLED},
    TaskExecutionStatus.VALIDATING: {
        TaskExecutionStatus.RUNNING,
        TaskExecutionStatus.FAILED,
        TaskExecutionStatus.CANCELLED,
    },
    TaskExecutionStatus.RUNNING: {
        TaskExecutionStatus.WAITING_APPROVAL,
        TaskExecutionStatus.PAUSED,
        TaskExecutionStatus.VALIDATING_RESULT,
        TaskExecutionStatus.FAILED,
        TaskExecutionStatus.CANCELLED,
        TaskExecutionStatus.TIMED_OUT,
    },
    TaskExecutionStatus.WAITING_APPROVAL: {
        TaskExecutionStatus.RUNNING,
        TaskExecutionStatus.FAILED,
        TaskExecutionStatus.CANCELLED,
        TaskExecutionStatus.TIMED_OUT,
    },
    TaskExecutionStatus.PAUSED: {
        TaskExecutionStatus.RUNNING,
        TaskExecutionStatus.FAILED,
        TaskExecutionStatus.CANCELLED,
        TaskExecutionStatus.TIMED_OUT,
    },
    TaskExecutionStatus.VALIDATING_RESULT: {
        TaskExecutionStatus.RUNNING,
        TaskExecutionStatus.SUCCEEDED,
        TaskExecutionStatus.FAILED,
        TaskExecutionStatus.CANCELLED,
        TaskExecutionStatus.TIMED_OUT,
    },
    TaskExecutionStatus.SUCCEEDED: set(),
    TaskExecutionStatus.FAILED: set(),
    TaskExecutionStatus.CANCELLED: set(),
    TaskExecutionStatus.TIMED_OUT: set(),
}

ATTEMPT_TRANSITIONS: dict[AttemptStatus, set[AttemptStatus]] = {
    AttemptStatus.PENDING: {
        AttemptStatus.PREPARING_SANDBOX,
        AttemptStatus.CANCELLED,
        AttemptStatus.FAILED,
    },
    AttemptStatus.PREPARING_SANDBOX: {
        AttemptStatus.RUNNING,
        AttemptStatus.FAILED,
        AttemptStatus.CANCELLED,
        AttemptStatus.TIMED_OUT,
    },
    AttemptStatus.RUNNING: {
        AttemptStatus.CHECKPOINTED,
        AttemptStatus.SUCCEEDED,
        AttemptStatus.FAILED,
        AttemptStatus.CANCELLED,
        AttemptStatus.TIMED_OUT,
        AttemptStatus.NEEDS_RECONCILIATION,
    },
    AttemptStatus.CHECKPOINTED: {
        AttemptStatus.RUNNING,
        AttemptStatus.SUCCEEDED,
        AttemptStatus.FAILED,
        AttemptStatus.CANCELLED,
        AttemptStatus.TIMED_OUT,
        AttemptStatus.NEEDS_RECONCILIATION,
    },
    AttemptStatus.SUCCEEDED: set(),
    AttemptStatus.FAILED: set(),
    AttemptStatus.CANCELLED: set(),
    AttemptStatus.TIMED_OUT: set(),
    AttemptStatus.NEEDS_RECONCILIATION: set(),
}


def can_transition_execution(current: TaskExecutionStatus, target: TaskExecutionStatus) -> bool:
    """判断 Execution 是否可跳转，位于 Harness Controller 状态写入前的检查环节。"""
    return target in EXECUTION_TRANSITIONS.get(current, set())


def transition_execution(
    execution: TaskExecution,
    target: TaskExecutionStatus,
    *,
    last_error: str | None = None,
    checkpoint_ref: str | None = None,
    result_ref: str | None = None,
) -> TaskExecution:
    """生成合法的新 Execution 状态，位于 Harness 状态机和 TaskStore 之间的流程边界。"""
    if not can_transition_execution(execution.status, target):
        raise InvalidTransitionError(
            f"illegal TaskExecution transition: {execution.status.value} -> {target.value}"
        )
    return replace(
        execution,
        status=target,
        last_error=last_error,
        checkpoint_ref=checkpoint_ref if checkpoint_ref is not None else execution.checkpoint_ref,
        result_ref=result_ref if result_ref is not None else execution.result_ref,
        updated_at=utc_now(),
    )


def can_transition_attempt(current: AttemptStatus, target: AttemptStatus) -> bool:
    """判断 Attempt 是否可跳转，位于 Harness Worker 更新尝试状态前的检查环节。"""
    return target in ATTEMPT_TRANSITIONS.get(current, set())


def transition_attempt(
    attempt: Attempt,
    target: AttemptStatus,
    *,
    last_error: str | None = None,
    checkpoint_ref: str | None = None,
    sandbox_ref: str | None = None,
) -> Attempt:
    """生成合法的新 Attempt 状态，位于 Harness 执行尝试与持久化之间的流程边界。"""
    if not can_transition_attempt(attempt.status, target):
        raise InvalidTransitionError(f"illegal Attempt transition: {attempt.status.value} -> {target.value}")
    return replace(
        attempt,
        status=target,
        last_error=last_error,
        checkpoint_ref=checkpoint_ref if checkpoint_ref is not None else attempt.checkpoint_ref,
        sandbox_ref=sandbox_ref if sandbox_ref is not None else attempt.sandbox_ref,
        updated_at=utc_now(),
    )
