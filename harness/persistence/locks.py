"""Agent-Harness 单 Execution 执行租约。

该模块位于 Worker 领取 TaskExecution 与实际运行之间，通过到期时间和随机所有权令牌
保证同一时刻只有一个 Worker 可推进状态，并阻止过期 Worker 删除新持有者的租约。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from scheduler.path_security import resolve_within, safe_child

from harness.errors import LeaseConflictError, LeaseOwnershipError

from .atomic import _atomic_write_json, _exclusive_lock, _read_json


@dataclass(frozen=True)
class ExecutionLease:
    """表示 Worker 的临时执行权，位于 Harness 调度与状态推进的并发边界。"""

    execution_id: str
    owner_id: str
    token: str
    acquired_at: str
    expires_at: str

    def to_dict(self) -> dict[str, str]:
        """序列化租约，供 Harness 跨进程校验执行权和到期时间。"""
        return {
            "execution_id": self.execution_id,
            "owner_id": self.owner_id,
            "token": self.token,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExecutionLease":
        """恢复租约，位于 Harness Worker 续租、接管或释放前的校验环节。"""
        return cls(
            execution_id=str(payload["execution_id"]),
            owner_id=str(payload["owner_id"]),
            token=str(payload["token"]),
            acquired_at=str(payload["acquired_at"]),
            expires_at=str(payload["expires_at"]),
        )


class ExecutionLeaseManager:
    """管理 Execution 租约，位于 Harness 单写者运行约束和故障接管流程。"""

    def __init__(self, root: Path, clock: Callable[[], datetime] | None = None):
        self.root = root.resolve()
        self.executions_dir = resolve_within(self.root, ".loop", "harness", "executions")
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def acquire(self, execution_id: str, owner_id: str, *, ttl_seconds: int = 30) -> ExecutionLease:
        """获取或接管到期租约，位于 Harness Worker 开始推进 Execution 之前。"""
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        path = self._lease_path(execution_id)
        with _exclusive_lock(path):
            current = self._load_optional(path)
            now = self._now()
            if current is not None and self._parse(current.expires_at) > now:
                raise LeaseConflictError(f"Execution lease is held by {current.owner_id}: {execution_id}")
            lease = ExecutionLease(
                execution_id=execution_id,
                owner_id=owner_id,
                token=f"lease-{uuid4().hex}",
                acquired_at=now.isoformat(),
                expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
            )
            _atomic_write_json(path, lease.to_dict())
            return lease

    def renew(self, lease: ExecutionLease, *, ttl_seconds: int = 30) -> ExecutionLease:
        """续期当前持有者租约，位于 Harness 长任务运行期间的心跳环节。"""
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        path = self._lease_path(lease.execution_id)
        with _exclusive_lock(path):
            current = self._require_owner(path, lease)
            now = self._now()
            if self._parse(current.expires_at) <= now:
                raise LeaseOwnershipError(f"Execution lease expired: {lease.execution_id}")
            renewed = ExecutionLease(
                execution_id=current.execution_id,
                owner_id=current.owner_id,
                token=current.token,
                acquired_at=current.acquired_at,
                expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
            )
            _atomic_write_json(path, renewed.to_dict())
            return renewed

    def release(self, lease: ExecutionLease) -> None:
        """仅由当前令牌持有者释放租约，位于 Harness Execution 停止推进后的收尾环节。"""
        path = self._lease_path(lease.execution_id)
        with _exclusive_lock(path):
            self._require_owner(path, lease)
            path.unlink()

    def inspect(self, execution_id: str) -> ExecutionLease | None:
        """读取当前租约，位于 Harness 运维诊断和恢复决策的只读环节。"""
        return self._load_optional(self._lease_path(execution_id))

    def assert_owner(self, lease: ExecutionLease) -> None:
        """确认租约仍有效，位于 Harness Controller 每次状态推进前的并发门禁。"""
        path = self._lease_path(lease.execution_id)
        current = self._require_owner(path, lease)
        if self._parse(current.expires_at) <= self._now():
            raise LeaseOwnershipError(f"Execution lease expired: {lease.execution_id}")

    def _now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    def _parse(self, value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)

    def _lease_path(self, execution_id: str) -> Path:
        execution_dir = safe_child(self.executions_dir, execution_id, "execution_id")
        return resolve_within(execution_dir, "lease.json")

    def _load_optional(self, path: Path) -> ExecutionLease | None:
        if not path.is_file():
            return None
        return ExecutionLease.from_dict(_read_json(path))

    def _require_owner(self, path: Path, lease: ExecutionLease) -> ExecutionLease:
        current = self._load_optional(path)
        if current is None or current.token != lease.token or current.owner_id != lease.owner_id:
            raise LeaseOwnershipError(f"Execution lease ownership changed: {lease.execution_id}")
        return current
