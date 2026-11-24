"""Agent-Harness 不可变检查点存储。

该模块位于模型响应、ToolResult 或验证结果之后的安全提交边界，将恢复所需状态写成
不可变快照；后续服务重启只从已提交 Checkpoint 恢复，不猜测未完成副作用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scheduler.path_security import resolve_within, safe_child

from harness.contracts import utc_now
from harness.errors import AlreadyExistsError, NotFoundError

from .atomic import _atomic_write_json, _exclusive_lock, _read_json


@dataclass(frozen=True)
class Checkpoint:
    """保存一次安全恢复快照，位于 Harness Attempt 的可重放边界。"""

    checkpoint_id: str
    execution_id: str
    attempt_id: str
    stage: str
    state: dict[str, Any] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """序列化检查点，供 Harness 持久化恢复上下文和外部产物引用。"""
        return {
            "schema_version": "1.0",
            "checkpoint_id": self.checkpoint_id,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "stage": self.stage,
            "state": dict(self.state),
            "refs": dict(self.refs),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Checkpoint":
        """恢复检查点，位于 Harness 服务重启后重建安全执行上下文的环节。"""
        return cls(
            checkpoint_id=str(payload["checkpoint_id"]),
            execution_id=str(payload["execution_id"]),
            attempt_id=str(payload["attempt_id"]),
            stage=str(payload["stage"]),
            state=dict(payload.get("state", {}) or {}),
            refs={str(key): str(value) for key, value in (payload.get("refs", {}) or {}).items()},
            created_at=str(payload["created_at"]),
        )


class CheckpointStore:
    """管理不可变 Checkpoint，位于 Harness 状态恢复与副作用隔离流程。"""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.executions_dir = resolve_within(self.root, ".loop", "harness", "executions")

    def write(self, checkpoint: Checkpoint) -> Checkpoint:
        """首次写入检查点，位于 Harness 完成一个安全执行步骤后的提交环节。"""
        path = self._checkpoint_path(checkpoint.execution_id, checkpoint.checkpoint_id)
        with _exclusive_lock(path):
            if path.exists():
                raise AlreadyExistsError(f"Checkpoint already exists: {checkpoint.checkpoint_id}")
            _atomic_write_json(path, checkpoint.to_dict())
        return checkpoint

    def load(self, execution_id: str, checkpoint_id: str) -> Checkpoint:
        """加载检查点，位于 Harness Attempt 重建上下文的恢复环节。"""
        path = self._checkpoint_path(execution_id, checkpoint_id)
        if not path.is_file():
            raise NotFoundError(f"Checkpoint not found: {checkpoint_id}")
        return Checkpoint.from_dict(_read_json(path))

    def list(self, execution_id: str) -> list[Checkpoint]:
        """按创建时间列出检查点，位于 Harness 选择最后安全恢复点的流程环节。"""
        directory = self._checkpoints_dir(execution_id)
        if not directory.is_dir():
            return []
        checkpoints = [Checkpoint.from_dict(_read_json(path)) for path in directory.glob("*.json")]
        return sorted(checkpoints, key=lambda item: (item.created_at, item.checkpoint_id))

    def _checkpoints_dir(self, execution_id: str) -> Path:
        execution_dir = safe_child(self.executions_dir, execution_id, "execution_id")
        return resolve_within(execution_dir, "checkpoints")

    def _checkpoint_path(self, execution_id: str, checkpoint_id: str) -> Path:
        return safe_child(
            self._checkpoints_dir(execution_id),
            f"{checkpoint_id}.json",
            "checkpoint filename",
        )
