"""Agent-Harness Worker 与租约心跳基础设施。

该模块位于 TaskRunner 长任务执行和恢复调度之间，持续续租单 Execution 的所有权，
避免模型或工具调用超过初始 TTL 后被第二个 Worker 重复领取。
"""

from __future__ import annotations

import threading
from typing import Callable, TYPE_CHECKING

from .persistence import ExecutionLease, ExecutionLeaseManager

if TYPE_CHECKING:
    from .controller import HarnessController
    from .integration import AgentProfile
    from .recovery import Reconciler
    from .task_runner import HarnessTaskRunner


class LeaseHeartbeat:
    """后台续租 Execution，位于 Agent Loop 每轮执行期间的并发控制层。"""

    def __init__(
        self,
        manager: ExecutionLeaseManager,
        lease: ExecutionLease,
        *,
        ttl_seconds: int,
        interval_seconds: float | None = None,
    ) -> None:
        self._manager = manager
        self._lease = lease
        self._ttl_seconds = ttl_seconds
        self._interval = interval_seconds or max(1.0, ttl_seconds / 3)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.failure: Exception | None = None

    def start(self) -> None:
        """启动心跳线程，位于 Harness Worker 获得租约后、进入 Sandbox 准备前。"""
        self._thread = threading.Thread(target=self._run, name="harness-lease-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止心跳线程，位于 Harness TaskResult 提交或异常清理后的租约释放前。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._interval + 1.0))

    def _run(self) -> None:
        """按固定间隔续租，位于 Harness Worker 的后台所有权维持循环。"""
        while not self._stop.wait(self._interval):
            try:
                self._lease = self._manager.renew(self._lease, ttl_seconds=self._ttl_seconds)
            except Exception as exc:  # 心跳失败由前台安全边界在下一次租约校验时拒绝继续写入。
                self.failure = exc
                self._stop.set()


class RecoveryWorker:
    """执行 Reconciler 已确认安全的恢复项，位于恢复判定与 TaskRunner.resume 之间。"""

    def __init__(
        self,
        controller: "HarnessController",
        reconciler: "Reconciler",
        runner: "HarnessTaskRunner",
    ) -> None:
        self._controller = controller
        self._reconciler = reconciler
        self._runner = runner

    def recover_once(
        self,
        profile_resolver: Callable[[str], "AgentProfile"],
    ) -> list[str]:
        """扫描并恢复一批 Execution，只执行 RESUMABLE 项且保留其他项供人工治理。"""
        from .recovery import RecoveryStatus

        resumed: list[str] = []
        for assessment in self._reconciler.scan():
            if assessment.status != RecoveryStatus.RESUMABLE:
                continue
            execution = self._controller.task_store.load_execution(assessment.execution_id)
            task = self._controller.task_store.load_task(execution.task_id)
            self._runner.resume(execution.execution_id, profile_resolver(task.agent_profile.agent_id))
            resumed.append(execution.execution_id)
        return resumed

