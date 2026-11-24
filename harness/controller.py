"""Agent-Harness 阶段 1 生命周期控制器。

该模块位于 GraphFlow 适配层与持久化基础设施之间，统一创建 Execution/Attempt、校验
租约、调用状态机、提交 Checkpoint 并追加事件，禁止上层绕过生命周期约束直接改状态。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .contracts import Attempt, AttemptStatus, Task, TaskExecution, TaskExecutionStatus, utc_now
from .cancellation import CancellationToken
from .errors import HarnessError, InvalidTransitionError, VersionConflictError
from .models import ModelUsage
from .persistence import (
    Checkpoint,
    CheckpointStore,
    EventStore,
    ExecutionLease,
    ExecutionLeaseManager,
    TaskStore,
)
from .state_machine import ATTEMPT_TERMINAL_STATUSES, transition_attempt, transition_execution

if TYPE_CHECKING:
    from .sandbox import SandboxArtifactManifest, SandboxManager


class HarnessController:
    """协调单 Task 生命周期，位于 GraphFlow 与未来 Agent Loop 之间的控制层。"""

    def __init__(self, root: Path):
        self.task_store = TaskStore(root)
        self.event_store = EventStore(root)
        self.checkpoint_store = CheckpointStore(root)
        self.lease_manager = ExecutionLeaseManager(root)

    def register_task(self, task: Task) -> Task:
        """登记不可变 Task，位于 GraphFlow Node 转换为 Harness 输入契约的入口环节。"""
        return self.task_store.create_task(task)

    def create_execution(
        self,
        task_id: str,
        *,
        execution_id: str | None = None,
        attempt_id: str | None = None,
    ) -> tuple[TaskExecution, Attempt]:
        """创建 Execution 和首个 Attempt，位于 Harness 单 Task 执行生命周期起点。"""
        self.task_store.load_task(task_id)
        selected_execution_id = execution_id or f"exec-{uuid4().hex}"
        selected_attempt_id = attempt_id or f"attempt-{uuid4().hex}"
        attempt = Attempt(
            attempt_id=selected_attempt_id,
            execution_id=selected_execution_id,
            sequence=1,
        )
        execution = TaskExecution(
            execution_id=selected_execution_id,
            task_id=task_id,
            active_attempt_id=selected_attempt_id,
            attempt_ids=(selected_attempt_id,),
        )

        # Execution 是恢复入口，Attempt 是执行细节；两者都落盘后才发布 created 事件。
        self.task_store.create_execution(execution)
        self.task_store.create_attempt(attempt)
        self.event_store.append(
            selected_execution_id,
            "execution.created",
            attempt_id=selected_attempt_id,
            payload={"task_id": task_id, "attempt_sequence": 1},
        )
        return execution, attempt

    def create_attempt(
        self,
        execution_id: str,
        lease: ExecutionLease,
        *,
        attempt_id: str | None = None,
    ) -> tuple[TaskExecution, Attempt]:
        """为重试创建全新 Attempt，位于 Harness 故障恢复而非原地覆盖的流程环节。"""
        self._assert_lease(execution_id, lease)
        execution = self.task_store.load_execution(execution_id)
        if execution.active_attempt_id:
            active = self.task_store.load_attempt(execution_id, execution.active_attempt_id)
            if active.status not in ATTEMPT_TERMINAL_STATUSES:
                raise InvalidTransitionError("active Attempt must be terminal before creating a retry")
        selected_attempt_id = attempt_id or f"attempt-{uuid4().hex}"
        attempt = Attempt(
            attempt_id=selected_attempt_id,
            execution_id=execution_id,
            sequence=len(execution.attempt_ids) + 1,
        )
        self.task_store.create_attempt(attempt)
        updated = replace(
            execution,
            active_attempt_id=selected_attempt_id,
            attempt_ids=execution.attempt_ids + (selected_attempt_id,),
        )
        saved_execution = self.task_store.save_execution(
            updated,
            expected_version=execution.state_version,
        )
        self.event_store.append(
            execution_id,
            "attempt.created",
            attempt_id=selected_attempt_id,
            payload={"attempt_sequence": attempt.sequence},
        )
        return saved_execution, attempt

    def transition_execution(
        self,
        execution_id: str,
        target: TaskExecutionStatus,
        lease: ExecutionLease,
        *,
        last_error: str | None = None,
        result_ref: str | None = None,
    ) -> TaskExecution:
        """合法推进 Execution，位于 Harness 租约门禁与原子状态提交之间的流程环节。"""
        self._assert_lease(execution_id, lease)
        current = self.task_store.load_execution(execution_id)
        try:
            changed = transition_execution(
                current,
                target,
                last_error=last_error,
                result_ref=result_ref,
            )
        except InvalidTransitionError:
            # 拒绝也是 Harness 审计事实，必须在返回错误前留下来源状态和目标状态。
            self.event_store.append(
                execution_id,
                "execution.transition_rejected",
                attempt_id=current.active_attempt_id,
                payload={"from": current.status.value, "to": target.value},
            )
            raise
        saved = self.task_store.save_execution(changed, expected_version=current.state_version)
        self.event_store.append(
            execution_id,
            "execution.status_changed",
            attempt_id=saved.active_attempt_id,
            payload={"from": current.status.value, "to": saved.status.value},
        )
        return saved

    def transition_attempt(
        self,
        execution_id: str,
        attempt_id: str,
        target: AttemptStatus,
        lease: ExecutionLease,
        *,
        last_error: str | None = None,
        sandbox_ref: str | None = None,
    ) -> Attempt:
        """合法推进 Attempt，位于 Harness Sandbox/Agent Loop 状态提交的控制环节。"""
        self._assert_lease(execution_id, lease)
        current = self.task_store.load_attempt(execution_id, attempt_id)
        try:
            changed = transition_attempt(
                current,
                target,
                last_error=last_error,
                sandbox_ref=sandbox_ref,
            )
        except InvalidTransitionError:
            # Attempt 非法跳转不可静默失败，事件供恢复流程判断该动作从未提交。
            self.event_store.append(
                execution_id,
                "attempt.transition_rejected",
                attempt_id=attempt_id,
                payload={"from": current.status.value, "to": target.value},
            )
            raise
        saved = self.task_store.save_attempt(changed, expected_version=current.state_version)
        self.event_store.append(
            execution_id,
            "attempt.status_changed",
            attempt_id=attempt_id,
            payload={"from": current.status.value, "to": saved.status.value},
        )
        return saved

    def write_checkpoint(
        self,
        execution_id: str,
        attempt_id: str,
        lease: ExecutionLease,
        *,
        stage: str,
        state: dict[str, Any],
        refs: dict[str, str] | None = None,
        checkpoint_id: str | None = None,
    ) -> Checkpoint:
        """提交安全检查点并关联状态，位于 Harness 可恢复步骤完成后的提交环节。"""
        self._assert_lease(execution_id, lease)
        execution = self.task_store.load_execution(execution_id)
        attempt = self.task_store.load_attempt(execution_id, attempt_id)
        if attempt.status not in {AttemptStatus.RUNNING, AttemptStatus.CHECKPOINTED}:
            raise InvalidTransitionError(
                f"Attempt cannot checkpoint from status: {attempt.status.value}"
            )
        selected_checkpoint_id = checkpoint_id or f"checkpoint-{uuid4().hex}"
        checkpoint = Checkpoint(
            checkpoint_id=selected_checkpoint_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
            stage=stage,
            state=dict(state),
            refs=dict(refs or {}),
        )
        self.checkpoint_store.write(checkpoint)

        # RUNNING 首次提交后进入 CHECKPOINTED；后续检查点保持该状态并只更新引用。
        if attempt.status == AttemptStatus.RUNNING:
            changed_attempt = transition_attempt(
                attempt,
                AttemptStatus.CHECKPOINTED,
                checkpoint_ref=selected_checkpoint_id,
            )
        elif attempt.status == AttemptStatus.CHECKPOINTED:
            changed_attempt = replace(attempt, checkpoint_ref=selected_checkpoint_id)
        self.task_store.save_attempt(changed_attempt, expected_version=attempt.state_version)
        changed_execution = replace(execution, checkpoint_ref=selected_checkpoint_id)
        self.task_store.save_execution(changed_execution, expected_version=execution.state_version)
        self.event_store.append(
            execution_id,
            "checkpoint.committed",
            attempt_id=attempt_id,
            payload={"checkpoint_id": selected_checkpoint_id, "stage": stage},
        )
        return checkpoint

    def start_execution(
        self,
        execution_id: str,
        lease: ExecutionLease,
        sandboxes: "SandboxManager",
    ) -> tuple[TaskExecution, Attempt]:
        """准备 Sandbox 并启动 Attempt，位于 Harness Agent Loop 开始前的控制流程。"""
        execution = self.transition_execution(execution_id, TaskExecutionStatus.VALIDATING, lease)
        if execution.active_attempt_id is None:
            raise InvalidTransitionError("Execution has no active Attempt")
        attempt_id = execution.active_attempt_id
        self.transition_attempt(execution_id, attempt_id, AttemptStatus.PREPARING_SANDBOX, lease)
        try:
            handle = sandboxes.create(execution_id, attempt_id)
        except Exception as exc:
            self.transition_attempt(
                execution_id,
                attempt_id,
                AttemptStatus.FAILED,
                lease,
                last_error=str(exc),
            )
            self.transition_execution(
                execution_id,
                TaskExecutionStatus.FAILED,
                lease,
                last_error=str(exc),
            )
            raise
        attempt = self.transition_attempt(
            execution_id,
            attempt_id,
            AttemptStatus.RUNNING,
            lease,
            sandbox_ref=handle.sandbox_id,
        )
        self.event_store.append(
            execution_id,
            "sandbox.created",
            attempt_id=attempt_id,
            payload={"sandbox_id": handle.sandbox_id, "runtime": type(sandboxes).__name__},
        )
        execution = self.transition_execution(execution_id, TaskExecutionStatus.RUNNING, lease)
        return execution, attempt

    def record_model_usage(
        self,
        execution_id: str,
        attempt_id: str,
        lease: ExecutionLease,
        usage: ModelUsage,
    ) -> Attempt:
        """原子累计一轮模型用量，位于 Harness Model Gateway 返回后的预算提交环节。"""
        self._assert_lease(execution_id, lease)
        current = self.task_store.load_attempt(execution_id, attempt_id)
        combined_cost = (
            None
            if current.cost_used is None or usage.cost is None
            else current.cost_used + usage.cost
        )
        changed = replace(
            current,
            rounds_used=current.rounds_used + 1,
            model_tokens_used=current.model_tokens_used + usage.total_tokens,
            cost_used=combined_cost,
        )
        saved = self.task_store.save_attempt(changed, expected_version=current.state_version)
        self.event_store.append(
            execution_id,
            "budget.model_committed",
            attempt_id=attempt_id,
            payload={"usage": usage.to_dict(), "rounds_used": saved.rounds_used},
        )
        return saved

    def record_tool_usage(
        self,
        execution_id: str,
        attempt_id: str,
        lease: ExecutionLease,
    ) -> Attempt:
        """原子累计 ToolCall，位于 Harness Router 返回 ToolResult 后的预算提交环节。"""
        self._assert_lease(execution_id, lease)
        current = self.task_store.load_attempt(execution_id, attempt_id)
        changed = replace(current, tool_calls_used=current.tool_calls_used + 1)
        saved = self.task_store.save_attempt(changed, expected_version=current.state_version)
        self.event_store.append(
            execution_id,
            "budget.tool_committed",
            attempt_id=attempt_id,
            payload={"tool_calls_used": saved.tool_calls_used},
        )
        return saved

    def request_cancel(
        self,
        execution_id: str,
        *,
        reason: str = "user_requested",
        sandboxes: "SandboxManager | None" = None,
        cancellation: CancellationToken | None = None,
    ) -> TaskExecution:
        """持久化并传播取消，位于 Harness 用户请求到活动 Sandbox 的控制流程。"""
        saved: TaskExecution | None = None
        for _ in range(3):
            current = self.task_store.load_execution(execution_id)
            if current.cancel_requested_at is not None:
                saved = current
                break
            changed = replace(
                current,
                cancel_requested_at=utc_now(),
                cancellation_reason=reason,
            )
            try:
                saved = self.task_store.save_execution(changed, expected_version=current.state_version)
                break
            except VersionConflictError:
                continue
        if saved is None:
            raise VersionConflictError(f"Unable to persist cancellation request: {execution_id}")
        self.event_store.append(
            execution_id,
            "execution.cancel_requested",
            attempt_id=saved.active_attempt_id,
            payload={"reason": reason},
        )
        if cancellation is not None:
            cancellation.cancel(reason)
        if sandboxes is not None and saved.active_attempt_id is not None:
            attempt = self.task_store.load_attempt(execution_id, saved.active_attempt_id)
            if attempt.sandbox_ref:
                try:
                    sandboxes.cancel(attempt.sandbox_ref)
                except (HarnessError, OSError, ValueError) as exc:
                    self.event_store.append(
                        execution_id,
                        "sandbox.cancel_failed",
                        attempt_id=attempt.attempt_id,
                        payload={"error_type": type(exc).__name__},
                    )
        return saved

    def is_cancel_requested(self, execution_id: str) -> bool:
        """查询持久取消标记，位于 Harness Agent Loop 每个安全边界的停止检查环节。"""
        return self.task_store.load_execution(execution_id).cancel_requested_at is not None

    def request_pause(self, execution_id: str, *, reason: str = "user_requested") -> TaskExecution:
        """持久化暂停意图，位于 Harness 用户请求到 Agent Loop 安全边界的控制流程。"""
        for _ in range(3):
            current = self.task_store.load_execution(execution_id)
            if current.pause_requested_at is not None:
                return current
            changed = replace(current, pause_requested_at=utc_now(), pause_reason=reason)
            try:
                saved = self.task_store.save_execution(changed, expected_version=current.state_version)
                self.event_store.append(
                    execution_id,
                    "execution.pause_requested",
                    attempt_id=saved.active_attempt_id,
                    payload={"reason": reason},
                )
                return saved
            except VersionConflictError:
                continue
        raise VersionConflictError(f"Unable to persist pause request: {execution_id}")

    def is_pause_requested(self, execution_id: str) -> bool:
        """查询持久暂停标记，位于 Harness Agent Loop 每个安全 Checkpoint 的控制环节。"""
        return self.task_store.load_execution(execution_id).pause_requested_at is not None

    def pause_execution(self, execution_id: str, lease: ExecutionLease) -> TaskExecution:
        """将运行置为 PAUSED，位于 Harness Agent Loop 已停止且保留 Sandbox 的安全边界。"""
        return self.transition_execution(execution_id, TaskExecutionStatus.PAUSED, lease)

    def resume_execution(self, execution_id: str, lease: ExecutionLease) -> TaskExecution:
        """清除暂停标记并恢复 RUNNING，位于 Harness 从 Checkpoint 继续 Agent Loop 的入口。"""
        self._assert_lease(execution_id, lease)
        current = self.task_store.load_execution(execution_id)
        changed = transition_execution(current, TaskExecutionStatus.RUNNING)
        changed = replace(changed, pause_requested_at=None, pause_reason=None)
        saved = self.task_store.save_execution(changed, expected_version=current.state_version)
        self.event_store.append(
            execution_id,
            "execution.resumed",
            attempt_id=saved.active_attempt_id,
            payload={"from": current.status.value, "to": saved.status.value},
        )
        return saved

    def load_last_checkpoint(self, execution_id: str) -> Checkpoint | None:
        """加载最后安全恢复点，位于 Harness 服务重启后恢复 Agent Loop 的流程环节。"""
        execution = self.task_store.load_execution(execution_id)
        if execution.checkpoint_ref is None:
            return None
        return self.checkpoint_store.load(execution_id, execution.checkpoint_ref)

    def enter_validation(
        self,
        execution_id: str,
        lease: ExecutionLease,
    ) -> TaskExecution:
        """进入结果验收态，位于 Agent 完成提议后、Sandbox 回收前的流程关口。"""
        return self.transition_execution(
            execution_id,
            TaskExecutionStatus.VALIDATING_RESULT,
            lease,
        )

    def finish_validation(
        self,
        execution_id: str,
        attempt_id: str,
        lease: ExecutionLease,
        sandboxes: "SandboxManager",
        *,
        passed: bool,
        error: str | None = None,
    ) -> tuple[TaskExecution, Attempt, "SandboxArtifactManifest | None"]:
        """提交验收终态并回收 Sandbox，位于 Harness Validator 与 TaskResult 生成之间。"""
        attempt_target = AttemptStatus.SUCCEEDED if passed else AttemptStatus.FAILED
        execution_target = TaskExecutionStatus.SUCCEEDED if passed else TaskExecutionStatus.FAILED
        attempt = self.transition_attempt(
            execution_id,
            attempt_id,
            attempt_target,
            lease,
            last_error=error,
        )
        execution = self.transition_execution(
            execution_id,
            execution_target,
            lease,
            last_error=error,
        )
        manifest = sandboxes.destroy(attempt.sandbox_ref) if attempt.sandbox_ref else None
        if manifest is not None:
            self.event_store.append(
                execution_id,
                "sandbox.destroyed",
                attempt_id=attempt_id,
                payload={"files": len(manifest.files), "total_bytes": manifest.total_bytes},
            )
        return execution, attempt, manifest

    def attach_result_ref(
        self,
        execution_id: str,
        result_ref: str,
        lease: ExecutionLease,
    ) -> TaskExecution:
        """为终态 Execution 绑定结果引用，位于 Harness TaskResult 原子写入后的提交环节。"""
        self._assert_lease(execution_id, lease)
        current = self.task_store.load_execution(execution_id)
        changed = replace(current, result_ref=result_ref, updated_at=utc_now())
        saved = self.task_store.save_execution(changed, expected_version=current.state_version)
        self.event_store.append(
            execution_id,
            "execution.result_attached",
            attempt_id=saved.active_attempt_id,
            payload={"result_ref": result_ref},
        )
        return saved

    def finalize_execution(
        self,
        execution_id: str,
        attempt_id: str,
        lease: ExecutionLease,
        sandboxes: "SandboxManager",
        *,
        outcome: str,
        error: str | None = None,
    ) -> tuple[TaskExecution, Attempt, "SandboxArtifactManifest | None"]:
        """映射 Loop 结果并回收 Sandbox，位于 Harness Agent 执行结束后的收尾流程。"""
        execution = self.task_store.load_execution(execution_id)
        if execution.cancel_requested_at is not None:
            outcome = "cancelled"
            error = execution.cancellation_reason
        mapping = {
            "completed": (AttemptStatus.SUCCEEDED, TaskExecutionStatus.VALIDATING_RESULT),
            "cancelled": (AttemptStatus.CANCELLED, TaskExecutionStatus.CANCELLED),
            "timed_out": (AttemptStatus.TIMED_OUT, TaskExecutionStatus.TIMED_OUT),
            "needs_reconciliation": (AttemptStatus.NEEDS_RECONCILIATION, TaskExecutionStatus.FAILED),
            "budget_exhausted": (AttemptStatus.FAILED, TaskExecutionStatus.FAILED),
            "failed": (AttemptStatus.FAILED, TaskExecutionStatus.FAILED),
        }
        if outcome not in mapping:
            raise ValueError(f"unknown Agent Loop outcome: {outcome}")
        attempt_target, execution_target = mapping[outcome]
        attempt = self.transition_attempt(
            execution_id,
            attempt_id,
            attempt_target,
            lease,
            last_error=error,
        )
        execution = self.transition_execution(
            execution_id,
            execution_target,
            lease,
            last_error=error,
        )
        manifest = None
        if attempt.sandbox_ref:
            manifest = sandboxes.destroy(attempt.sandbox_ref)
            self.event_store.append(
                execution_id,
                "sandbox.destroyed",
                attempt_id=attempt_id,
                payload={"files": len(manifest.files), "total_bytes": manifest.total_bytes},
            )
        return execution, attempt, manifest

    def _assert_lease(self, execution_id: str, lease: ExecutionLease) -> None:
        if lease.execution_id != execution_id:
            raise InvalidTransitionError("lease does not belong to the target Execution")
        self.lease_manager.assert_owner(lease)
