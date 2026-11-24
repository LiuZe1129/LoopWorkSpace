"""Agent-Harness Sandbox 生命周期管理器。

该模块位于 Tool Router 与 SandboxRuntime 之间，为每个 Attempt 创建并复用一个工作区，
执行后检查磁盘限额，并在销毁容器前生成不跟随符号链接的 Artifact 哈希清单。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scheduler.path_security import resolve_within, safe_child

from harness.errors import SandboxNotFoundError, WorkspaceLimitError
from harness.persistence.atomic import _atomic_write_json, _read_json

from .base import (
    SandboxExecRequest,
    SandboxExecResult,
    SandboxHandle,
    SandboxInfo,
    SandboxRuntime,
    SandboxSpec,
)


@dataclass(frozen=True)
class SandboxArtifactManifest:
    """描述 Sandbox 工作区产物，位于 Harness 容器销毁前的 Artifact 收集边界。"""

    sandbox_id: str
    execution_id: str
    attempt_id: str
    files: tuple[dict[str, Any], ...]
    total_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """序列化产物清单，供 Harness TaskResult 和 GraphFlow Artifact 映射流程引用。"""
        return {
            "sandbox_id": self.sandbox_id,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "files": list(self.files),
            "total_bytes": self.total_bytes,
        }


class SandboxManager:
    """管理单 Attempt Sandbox，位于 Harness Tool 执行全生命周期控制层。"""

    def __init__(
        self,
        root: Path,
        runtime: SandboxRuntime,
        *,
        image: str = "python:3.12-alpine",
        max_workspace_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        self.root = root.resolve()
        self._runtime = runtime
        self._image = image
        self._max_workspace_bytes = max_workspace_bytes
        self._sandboxes_root = resolve_within(self.root, ".loop", "harness", "sandboxes")
        self._executions_root = resolve_within(self.root, ".loop", "harness", "executions")
        self._handles: dict[str, tuple[str, SandboxHandle]] = {}

    def create(self, execution_id: str, attempt_id: str) -> SandboxHandle:
        """创建并绑定单 Attempt Sandbox，位于 Harness Attempt PREPARING_SANDBOX 环节。"""
        sandbox_id = f"sandbox-{execution_id}-{attempt_id}"
        if sandbox_id in self._handles:
            raise ValueError(f"Sandbox already exists: {sandbox_id}")
        workspace = self.workspace_path(execution_id, attempt_id)
        workspace.mkdir(parents=True, exist_ok=True)
        # 工作区是专用挂载目录；开放容器非 root 用户写权限，不扩展到仓库或其他 Task。
        try:
            workspace.chmod(0o777)
        except OSError:
            pass
        handle = self._runtime.create(
            SandboxSpec(
                sandbox_id=sandbox_id,
                attempt_id=attempt_id,
                workspace=workspace,
                image=self._image,
            )
        )
        self._handles[sandbox_id] = (execution_id, handle)
        _atomic_write_json(
            self._state_path(execution_id, attempt_id),
            {
                "sandbox_id": handle.sandbox_id,
                "execution_id": execution_id,
                "attempt_id": attempt_id,
                "runtime_id": handle.runtime_id,
                # 镜像引用与容器 ID 位于 Harness 运行时证据链，便于复现实际 Sandbox 环境。
                "image": self._image,
                "workspace": workspace.as_posix(),
                "status": "running",
            },
        )
        return handle

    def workspace_path(self, execution_id: str, attempt_id: str) -> Path:
        """返回 Attempt 隔离工作区，位于 Harness 源码快照准备与 Sandbox 挂载的交接点。"""
        execution_dir = safe_child(self._sandboxes_root, execution_id, "execution_id")
        attempt_dir = safe_child(execution_dir, attempt_id, "attempt_id")
        return resolve_within(attempt_dir, "workspace")

    def restore(self, execution_id: str, attempt_id: str, sandbox_id: str) -> SandboxHandle:
        """重新连接持久 Sandbox，位于 Harness 服务重启后的 Agent Loop 恢复环节。"""
        if sandbox_id in self._handles:
            if not self.owns(sandbox_id, execution_id, attempt_id):
                raise SandboxNotFoundError("Sandbox binding does not match Execution/Attempt")
            return self._handles[sandbox_id][1]
        state_path = self._state_path(execution_id, attempt_id)
        if not state_path.is_file():
            raise SandboxNotFoundError(f"Sandbox state not found: {sandbox_id}")
        payload = _read_json(state_path)
        if (
            payload.get("sandbox_id") != sandbox_id
            or payload.get("execution_id") != execution_id
            or payload.get("attempt_id") != attempt_id
        ):
            raise SandboxNotFoundError("Persisted Sandbox binding is inconsistent")
        workspace = resolve_within(
            safe_child(
                safe_child(self._sandboxes_root, execution_id, "execution_id"),
                attempt_id,
                "attempt_id",
            ),
            "workspace",
        )
        handle = SandboxHandle(sandbox_id, attempt_id, str(payload["runtime_id"]), workspace)
        info = self._runtime.inspect(handle)
        if info.status != "running":
            raise SandboxNotFoundError(f"Sandbox is not running: {info.status}")
        self._handles[sandbox_id] = (execution_id, handle)
        return handle

    def execute(self, sandbox_id: str, request: SandboxExecRequest) -> SandboxExecResult:
        """在已绑定容器内执行，位于 Harness Router 授权后和 ToolResult 生成前。"""
        _, handle = self._require(sandbox_id)
        result = self._runtime.exec(handle, request)
        if self._workspace_size(handle.workspace) > self._max_workspace_bytes:
            self._runtime.cancel(handle)
            raise WorkspaceLimitError(f"Sandbox workspace limit exceeded: {sandbox_id}")
        return result

    def cancel(self, sandbox_id: str) -> None:
        """取消容器内执行，位于 Harness Task 取消或 Tool 超时传播环节。"""
        _, handle = self._require(sandbox_id)
        self._runtime.cancel(handle)

    def destroy(self, sandbox_id: str) -> SandboxArtifactManifest:
        """收集 Artifact 后销毁容器，位于 Harness Attempt 进入终态的资源回收环节。"""
        execution_id, handle = self._require(sandbox_id)
        manifest = self._collect_manifest(execution_id, handle)
        attempts_dir = resolve_within(
            safe_child(self._executions_root, execution_id, "execution_id"),
            "attempts",
        )
        manifest_path = resolve_within(
            safe_child(attempts_dir, handle.attempt_id, "attempt_id"),
            "sandbox-artifacts.json",
        )
        _atomic_write_json(manifest_path, manifest.to_dict())
        self._runtime.destroy(handle)
        state_path = self._state_path(execution_id, handle.attempt_id)
        if state_path.is_file():
            payload = _read_json(state_path)
            payload["status"] = "destroyed"
            _atomic_write_json(state_path, payload)
        del self._handles[sandbox_id]
        return manifest

    def inspect(self, sandbox_id: str) -> SandboxInfo:
        """检查 Sandbox 状态，位于 Harness 运维诊断和恢复判断的只读环节。"""
        _, handle = self._require(sandbox_id)
        return self._runtime.inspect(handle)

    def handle(self, sandbox_id: str) -> SandboxHandle:
        """返回受管 Handle，位于 Harness Router 关联 ToolCall 与 Sandbox 的内部交接环节。"""
        return self._require(sandbox_id)[1]

    def owns(self, sandbox_id: str, execution_id: str, attempt_id: str) -> bool:
        """校验 Sandbox 归属，位于 Harness Router 防止跨 Execution/Attempt 串用容器的边界。"""
        selected = self._handles.get(sandbox_id)
        if selected is None:
            return False
        bound_execution_id, handle = selected
        return bound_execution_id == execution_id and handle.attempt_id == attempt_id

    def list_persisted(self) -> list[dict[str, Any]]:
        """列出 Sandbox 状态文件，位于 Harness Reconciler 发现孤儿容器的扫描环节。"""
        if not self._sandboxes_root.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self._sandboxes_root.glob("*/*/sandbox.json")):
            try:
                records.append(_read_json(path))
            except (OSError, ValueError):
                continue
        return records

    def _require(self, sandbox_id: str) -> tuple[str, SandboxHandle]:
        selected = self._handles.get(sandbox_id)
        if selected is None:
            raise SandboxNotFoundError(f"Sandbox not found: {sandbox_id}")
        return selected

    def _state_path(self, execution_id: str, attempt_id: str) -> Path:
        execution_dir = safe_child(self._sandboxes_root, execution_id, "execution_id")
        attempt_dir = safe_child(execution_dir, attempt_id, "attempt_id")
        return resolve_within(attempt_dir, "sandbox.json")

    def _workspace_size(self, workspace: Path) -> int:
        total = 0
        for path in workspace.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve()
            try:
                resolved.relative_to(workspace.resolve())
            except ValueError:
                continue
            total += path.stat().st_size
        return total

    def _collect_manifest(self, execution_id: str, handle: SandboxHandle) -> SandboxArtifactManifest:
        files: list[dict[str, Any]] = []
        total = 0
        workspace = handle.workspace.resolve()
        for path in sorted(workspace.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve()
            try:
                relative = resolved.relative_to(workspace)
            except ValueError:
                continue
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as handle_file:
                while True:
                    chunk = handle_file.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
            total += size
            files.append(
                {
                    "path": relative.as_posix(),
                    "size": size,
                    "sha256": digest.hexdigest(),
                }
            )
        return SandboxArtifactManifest(
            handle.sandbox_id,
            execution_id,
            handle.attempt_id,
            tuple(files),
            total,
        )
