from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EventLogger:
    def __init__(self, root: Path):
        self.root = root
        self.events_dir = root / ".loop" / "events"
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
        path = self.events_dir / f"{task_id}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return path

    def append_run(self, run_id: str, event_type: str, payload: dict[str, Any]) -> Path:
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
        return self.root / ".loop" / "runs" / run_id / "events.jsonl"

    def read_path(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
