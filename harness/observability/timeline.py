"""Agent-Harness Execution 统一时间线。

该模块位于 EventStore 与运维/评测消费者之间，通过 execution_id 重建 Task、Attempt、
Model、Tool、Policy、Sandbox 和 Validator 的因果摘要，且不暴露 Prompt、响应正文或文件内容。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.errors import NotFoundError
from harness.persistence import EventStore, ResultStore, TaskStore
from harness.persistence.atomic import _atomic_write_json

from .redaction import redact_value


@dataclass(frozen=True)
class TimelineEntry:
    """表示一条脱敏执行事实，位于 Harness EventStore 到统一时间线的转换边界。"""

    sequence: int
    timestamp: str
    category: str
    event_type: str
    attempt_id: str | None
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """序列化时间线条目，供 Harness CLI、评测报告和故障审计消费。"""
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "category": self.category,
            "event_type": self.event_type,
            "attempt_id": self.attempt_id,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class ExecutionTimeline:
    """封装单 Execution 的完整摘要，位于 Harness 可观测性查询输出边界。"""

    execution_id: str
    task_id: str
    execution_status: str
    result_status: str | None
    entries: tuple[TimelineEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        """序列化执行时间线，供 Harness 运维接口持久化或展示。"""
        return {
            "schema_version": "1.0",
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "execution_status": self.execution_status,
            "result_status": self.result_status,
            "entries": [entry.to_dict() for entry in self.entries],
        }


class TimelineBuilder:
    """按 execution_id 重建时间线，位于 Harness 持久化事实与诊断视图之间。"""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self._events = EventStore(root)
        self._tasks = TaskStore(root)
        self._results = ResultStore(root)

    def build(self, execution_id: str) -> ExecutionTimeline:
        """读取状态与事件生成脱敏时间线，位于 Harness 故障定位和评测查询环节。"""
        execution = self._tasks.load_execution(execution_id)
        result_status: str | None = None
        try:
            result_status = self._results.load_result(execution_id).status.value
        except NotFoundError:
            pass
        entries = tuple(
            TimelineEntry(
                event.sequence,
                event.timestamp,
                self._category(event.event_type),
                event.event_type,
                event.attempt_id,
                redact_value(event.payload),
            )
            for event in self._events.read(execution_id)
        )
        return ExecutionTimeline(
            execution_id,
            execution.task_id,
            execution.status.value,
            result_status,
            entries,
        )

    def write(self, execution_id: str) -> Path:
        """原子保存时间线快照，位于 Harness 运维导出和评测证据生成环节。"""
        timeline = self.build(execution_id)
        path = self.root / ".loop" / "harness" / "executions" / execution_id / "timeline.json"
        _atomic_write_json(path, timeline.to_dict())
        return path

    @staticmethod
    def _category(event_type: str) -> str:
        prefix = event_type.split(".", 1)[0]
        if prefix == "budget":
            return "model" if event_type == "budget.model_committed" else "tool"
        return {
            "execution": "task",
            "attempt": "attempt",
            "checkpoint": "recovery",
            "model": "model",
            "tool": "tool_policy",
            "sandbox": "sandbox",
            "validation": "validator",
        }.get(prefix, "runtime")
