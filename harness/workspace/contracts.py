"""Agent-Harness Workspace 稳定契约。

该模块位于本地源码快照、Sandbox Workspace 与 TaskResult 之间，定义可持久化的文件
清单、执行会话和 ChangeSet，支持恢复、冲突检查与审计。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.contracts import WorkspaceConfig, utc_now


@dataclass(frozen=True)
class WorkspaceFile:
    """记录单个快照文件，位于 Harness 源码基线与变更检测的内容寻址边界。"""

    path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """序列化文件摘要，供 Harness Snapshot Manifest 和冲突检查使用。"""
        return {"path": self.path, "sha256": self.sha256, "size_bytes": self.size_bytes}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkspaceFile":
        """恢复文件摘要，位于 Harness Worker 重启后重建 Workspace 会话的流程。"""
        return cls(str(payload["path"]), str(payload["sha256"]), int(payload["size_bytes"]))


@dataclass(frozen=True)
class WorkspaceSession:
    """描述一次源码快照会话，位于 Harness Execution 与单 Sandbox Workspace 之间。"""

    execution_id: str
    attempt_id: str
    source_root: Path
    snapshot_root: Path
    workspace_root: Path
    snapshot_id: str
    workspace_hash: str
    files: tuple[WorkspaceFile, ...]
    config: WorkspaceConfig
    created_at: str = ""

    def to_dict(self, repository_root: Path) -> dict[str, Any]:
        """序列化会话，供 Harness 恢复流程重建源码与 Sandbox 的关联。"""
        return {
            "schema_version": "1.0",
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "source_root": self.source_root.relative_to(repository_root).as_posix(),
            "snapshot_root": self.snapshot_root.relative_to(repository_root).as_posix(),
            "workspace_root": self.workspace_root.relative_to(repository_root).as_posix(),
            "snapshot_id": self.snapshot_id,
            "workspace_hash": self.workspace_hash,
            "files": [item.to_dict() for item in self.files],
            "config": self.config.to_dict(),
            "created_at": self.created_at or utc_now(),
        }


@dataclass(frozen=True)
class ChangeSet:
    """描述 Agent 对源码副本的变更，位于 Harness Validator 与本地工程回写之间。"""

    execution_id: str
    snapshot_id: str
    base_hash: str
    created: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]
    patch_ref: str
    patch_sha256: str
    applied: bool = False
    applied_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化 ChangeSet，供 Harness TaskResult、Artifact 和安全回写审计使用。"""
        return {
            "schema_version": "1.0",
            "execution_id": self.execution_id,
            "snapshot_id": self.snapshot_id,
            "base_hash": self.base_hash,
            "created": list(self.created),
            "modified": list(self.modified),
            "deleted": list(self.deleted),
            "patch_ref": self.patch_ref,
            "patch_sha256": self.patch_sha256,
            "applied": self.applied,
            "applied_at": self.applied_at,
        }
