"""Agent-Harness 服务重启恢复评估器。

该模块位于持久化状态、Lease、Tool Ledger 与 Sandbox 之间，扫描非终态 Execution，
识别可从 Checkpoint 恢复、取消待处理、活动所有者和副作用不确定等情况，不盲目重放。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from .contracts import TaskExecution
from .controller import HarnessController
from .errors import HarnessError
from .sandbox import SandboxManager
from .state_machine import EXECUTION_TERMINAL_STATUSES
from .tools.ledger import LedgerStatus, ToolLedger


class RecoveryStatus(str, Enum):
    """描述恢复判定，位于 Harness Reconciler 向 Worker 调度恢复任务的边界。"""

    TERMINAL = "terminal"
    ACTIVE_OWNER = "active_owner"
    RESUMABLE = "resumable"
    CANCEL_REQUESTED = "cancel_requested"
    NEEDS_RECONCILIATION = "needs_reconciliation"
    NO_CHECKPOINT = "no_checkpoint"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"


@dataclass(frozen=True)
class RecoveryAssessment:
    """封装一次恢复评估，位于 Harness Reconciler 与 Controller/运维的交接边界。"""

    execution_id: str
    attempt_id: str | None
    status: RecoveryStatus
    reason: str
    checkpoint_id: str | None = None
    lease_expired: bool = False


@dataclass(frozen=True)
class OrphanSandbox:
    """描述无对应 Execution 的容器，位于 Harness Reconciler 资源清理决策边界。"""

    sandbox_id: str
    execution_id: str
    attempt_id: str
    runtime_id: str


class Reconciler:
    """扫描并分类中断 Execution，位于 Harness 服务启动和定时恢复流程。"""

    def __init__(
        self,
        controller: HarnessController,
        ledger: ToolLedger,
        sandboxes: SandboxManager,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._controller = controller
        self._ledger = ledger
        self._sandboxes = sandboxes
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def scan(self) -> list[RecoveryAssessment]:
        """扫描全部 Execution，位于 Harness Reconciler 的服务启动和定时入口。"""
        return [self.assess(item) for item in self._controller.task_store.list_executions()]

    def find_orphan_sandboxes(self) -> list[OrphanSandbox]:
        """发现孤儿 Sandbox，位于 Harness Reconciler 定时资源治理的扫描环节。"""
        execution_ids = {
            execution.execution_id for execution in self._controller.task_store.list_executions()
        }
        orphans: list[OrphanSandbox] = []
        for payload in self._sandboxes.list_persisted():
            if payload.get("status") != "running":
                continue
            execution_id = str(payload.get("execution_id", ""))
            if execution_id not in execution_ids:
                orphans.append(
                    OrphanSandbox(
                        sandbox_id=str(payload.get("sandbox_id", "")),
                        execution_id=execution_id,
                        attempt_id=str(payload.get("attempt_id", "")),
                        runtime_id=str(payload.get("runtime_id", "")),
                    )
                )
        return orphans

    def assess(self, execution: TaskExecution) -> RecoveryAssessment:
        """评估一个 Execution，位于 Harness 判断是否安全恢复或需要人工处置的环节。"""
        if execution.status in EXECUTION_TERMINAL_STATUSES:
            return RecoveryAssessment(execution.execution_id, execution.active_attempt_id, RecoveryStatus.TERMINAL, "Execution 已终止")
        attempt_id = execution.active_attempt_id
        if attempt_id is None:
            return RecoveryAssessment(execution.execution_id, None, RecoveryStatus.NO_CHECKPOINT, "Execution 缺少 active Attempt")
        if execution.cancel_requested_at is not None:
            return RecoveryAssessment(execution.execution_id, attempt_id, RecoveryStatus.CANCEL_REQUESTED, "存在持久化取消请求")
        lease = self._controller.lease_manager.inspect(execution.execution_id)
        lease_expired = False
        if lease is not None:
            expires_at = datetime.fromisoformat(lease.expires_at)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            now = self._clock()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            lease_expired = expires_at <= now
            if not lease_expired:
                return RecoveryAssessment(
                    execution.execution_id,
                    attempt_id,
                    RecoveryStatus.ACTIVE_OWNER,
                    f"Execution 仍由 {lease.owner_id} 持有",
                )
        records = self._ledger.list_records(execution.execution_id, attempt_id)
        unsafe = [
            record
            for record in records
            if record.status == LedgerStatus.AMBIGUOUS
            or (record.status == LedgerStatus.STARTED and record.tool_name != "read_file")
        ]
        if unsafe:
            return RecoveryAssessment(
                execution.execution_id,
                attempt_id,
                RecoveryStatus.NEEDS_RECONCILIATION,
                "存在未完成或不确定的副作用 ToolCall",
                execution.checkpoint_ref,
                lease_expired,
            )
        if execution.checkpoint_ref is None:
            return RecoveryAssessment(
                execution.execution_id,
                attempt_id,
                RecoveryStatus.NO_CHECKPOINT,
                "没有已提交 Checkpoint",
                lease_expired=lease_expired,
            )
        attempt = self._controller.task_store.load_attempt(execution.execution_id, attempt_id)
        if attempt.sandbox_ref:
            try:
                self._sandboxes.restore(execution.execution_id, attempt_id, attempt.sandbox_ref)
            except HarnessError as exc:
                return RecoveryAssessment(
                    execution.execution_id,
                    attempt_id,
                    RecoveryStatus.SANDBOX_UNAVAILABLE,
                    str(exc),
                    execution.checkpoint_ref,
                    lease_expired,
                )
        return RecoveryAssessment(
            execution.execution_id,
            attempt_id,
            RecoveryStatus.RESUMABLE,
            "可从最后 Checkpoint 安全恢复",
            execution.checkpoint_ref,
            lease_expired,
        )
