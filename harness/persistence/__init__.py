"""Agent-Harness 持久化基础设施。

该包位于 Harness Controller 与文件系统之间，为 Task、Execution、Attempt、Event、
Lease 和 Checkpoint 提供原子写入、并发保护和服务重启后的恢复入口。
"""

from .event_store import EventStore, HarnessEvent
from .checkpoint_store import Checkpoint, CheckpointStore
from .locks import ExecutionLease, ExecutionLeaseManager
from .result_store import ResultStore
from .task_store import TaskStore

__all__ = [
    "Checkpoint",
    "CheckpointStore",
    "EventStore",
    "ExecutionLease",
    "ExecutionLeaseManager",
    "HarnessEvent",
    "ResultStore",
    "TaskStore",
]
