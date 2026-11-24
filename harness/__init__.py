"""Agent-Harness 单 Task 执行内核。

该包位于 GraphFlow 节点执行层内部：接收不可变 Task Contract，管理 TaskExecution
与 Attempt，并为后续 Agent Loop、Tool Router、Policy 和 Sandbox 提供稳定基础设施。
"""

from .contracts import (
    Attempt,
    AttemptStatus,
    Task,
    TaskExecution,
    TaskExecutionStatus,
    TaskResult,
    TaskResultStatus,
    WorkspaceConfig,
)
from .controller import HarnessController
from .task_runner import HarnessTaskRunner

__all__ = [
    "Attempt",
    "AttemptStatus",
    "HarnessController",
    "HarnessTaskRunner",
    "Task",
    "TaskExecution",
    "TaskExecutionStatus",
    "TaskResult",
    "TaskResultStatus",
    "WorkspaceConfig",
]
