from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .path_security import resolve_within, safe_child, validate_path_segment


class EventLogger:
    """记录 GraphFlow 节点事件，作为后续 Harness 细粒度事件流的外层时间线。

    阶段 0 先确保 task_id/run_id 无法越过事件目录；后续 Task、Model、Tool 和
    Policy 事件将由独立 Harness EventStore 承载，并通过 execution_id 与此处关联。
    """

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.events_dir = resolve_within(self.root, ".loop", "events")
        self.events_dir.mkdir(parents=True, exist_ok=True)

    def append(self, task_id: str, event_type: str, payload: dict[str, Any]) -> Path:
        """Append a legacy task-card event.

        New GraphRun events should use append_run(), but this method remains for
        migration-period task card compatibility.
        """
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **payload,
        }
        selected_task_id = validate_path_segment(task_id, "task_id")
        path = safe_child(self.events_dir, f"{selected_task_id}.jsonl", "task event filename")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return path

    def append_run(self, run_id: str, event_type: str, payload: dict[str, Any]) -> Path:
        """追加 GraphRun 事件，位于节点状态变化写入审计时间线的流程环节。"""
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **payload,
        }
        path = self.run_events_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return path

    def read(self, task_id: str) -> list[dict[str, Any]]:
        path = self.events_dir / f"{task_id}.jsonl"
        return self.read_path(path)

    def read_run(self, run_id: str) -> list[dict[str, Any]]:
        return self.read_path(self.run_events_path(run_id))

    def run_events_path(self, run_id: str) -> Path:
        """解析受控事件文件路径，防止 GraphRun 标识越过 Harness 工作区边界。"""
        runs_dir = resolve_within(self.root, ".loop", "runs")
        run_dir = safe_child(runs_dir, run_id, "run_id")
        return resolve_within(run_dir, "events.jsonl")

    def read_path(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
