"""Agent-Harness 追加式执行事件存储。

该模块位于 Harness Controller 的状态变更之后，以每个 Execution 独立 JSONL 时间线
记录因果信息，为服务重启恢复、问题审计和后续 GraphFlow 结果关联提供事实来源。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from scheduler.path_security import resolve_within, safe_child

from harness.contracts import utc_now

from .atomic import _append_json_line, _exclusive_lock


@dataclass(frozen=True)
class HarnessEvent:
    """描述一次 Harness 事实事件，位于状态持久化后的审计与恢复流程。"""

    event_id: str
    sequence: int
    timestamp: str
    execution_id: str
    event_type: str
    attempt_id: str | None = None
    causation_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化事件，供 Harness EventStore 写入稳定的执行时间线。"""
        return {
            "schema_version": "1.0",
            "event_id": self.event_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "event_type": self.event_type,
            "causation_id": self.causation_id,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HarnessEvent":
        """恢复事件，位于 Harness 重建时间线和定位最后安全状态的流程环节。"""
        return cls(
            event_id=str(payload["event_id"]),
            sequence=int(payload["sequence"]),
            timestamp=str(payload["timestamp"]),
            execution_id=str(payload["execution_id"]),
            attempt_id=payload.get("attempt_id"),
            event_type=str(payload["event_type"]),
            causation_id=payload.get("causation_id"),
            payload=dict(payload.get("payload", {}) or {}),
        )


class EventStore:
    """管理每个 Execution 的追加式事件流，位于 Harness 可观测性和恢复边界。"""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.executions_dir = resolve_within(self.root, ".loop", "harness", "executions")

    def append(
        self,
        execution_id: str,
        event_type: str,
        *,
        attempt_id: str | None = None,
        causation_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> HarnessEvent:
        """原子追加事件，位于 Harness 完成一次状态提交后的事实记录环节。"""
        path = self._events_path(execution_id)
        with _exclusive_lock(path):
            existing = self._read_path(path)
            event = HarnessEvent(
                event_id=f"evt-{uuid4().hex}",
                sequence=(existing[-1].sequence + 1) if existing else 1,
                timestamp=utc_now(),
                execution_id=execution_id,
                attempt_id=attempt_id,
                event_type=event_type,
                causation_id=causation_id,
                payload=dict(payload or {}),
            )
            _append_json_line(path, event.to_dict())
            return event

    def read(self, execution_id: str) -> list[HarnessEvent]:
        """按顺序读取事件，位于 Harness 重启恢复、审计和时间线查询环节。"""
        return self._read_path(self._events_path(execution_id))

    def _events_path(self, execution_id: str) -> Path:
        execution_dir = safe_child(self.executions_dir, execution_id, "execution_id")
        return resolve_within(execution_dir, "events.jsonl")

    def _read_path(self, path: Path) -> list[HarnessEvent]:
        if not path.is_file():
            return []
        events: list[HarnessEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(HarnessEvent.from_dict(json.loads(line)))
        return events
