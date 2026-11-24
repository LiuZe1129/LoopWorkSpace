"""Agent-Harness 本地源码快照与回写管理器。

该模块位于 GraphFlow Task 与 Sandbox 生命周期之间，把本地现有工程复制为不可变快照
和隔离工作区，并在确定性验收通过后执行冲突检测、ChangeSet 生成及可回滚原子回写。
"""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import subprocess
from dataclasses import replace
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from scheduler.path_security import resolve_within, safe_child
from scheduler.runs import relative_to_root

from harness.contracts import Task, utc_now
from harness.errors import WorkspaceConflictError, WorkspacePreparationError
from harness.persistence.atomic import _atomic_write_json, _atomic_write_text, _read_json

from .contracts import ChangeSet, WorkspaceFile, WorkspaceSession


class WorkspaceManager:
    """管理本地快照与验收后回写，位于 Harness 单 Task 工程副作用控制层。"""

    def __init__(self, root: Path, *, max_source_bytes: int = 512 * 1024 * 1024) -> None:
        self.root = root.resolve()
        self._max_source_bytes = max_source_bytes
        self._executions_root = resolve_within(self.root, ".loop", "harness", "executions")

    def prepare(
        self,
        task: Task,
        execution_id: str,
        attempt_id: str,
        workspace_root: Path,
    ) -> WorkspaceSession | None:
        """创建本地源码快照与隔离副本，位于 Harness Sandbox 启动前的准备环节。"""
        if task.workspace.mode == "none":
            return None
        source_root = self._resolve_source(task.workspace.source_root)
        execution_dir = safe_child(self._executions_root, execution_id, "execution_id")
        snapshot_root = resolve_within(execution_dir, "workspace-snapshot", "source")
        manifest_path = resolve_within(execution_dir, "workspace-snapshot", "manifest.json")
        if snapshot_root.exists() or manifest_path.exists():
            raise WorkspacePreparationError(f"Workspace snapshot already exists: {execution_id}")
        snapshot_root.mkdir(parents=True, exist_ok=False)
        workspace_root.mkdir(parents=True, exist_ok=True)
        files = self._collect_files(source_root, task)
        total = sum(item.size_bytes for item in files)
        if total > self._max_source_bytes:
            raise WorkspacePreparationError(
                f"Workspace source exceeds limit: {total} > {self._max_source_bytes}"
            )
        for item in files:
            source = resolve_within(source_root, *item.path.split("/"))
            snapshot = resolve_within(snapshot_root, *item.path.split("/"))
            workspace = resolve_within(workspace_root, *item.path.split("/"))
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            workspace.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, snapshot)
            shutil.copy2(snapshot, workspace)
        workspace_hash = self._manifest_hash(files)
        snapshot_id = f"snapshot-{workspace_hash[:24]}"
        session = WorkspaceSession(
            execution_id,
            attempt_id,
            source_root,
            snapshot_root,
            workspace_root.resolve(),
            snapshot_id,
            workspace_hash,
            files,
            task.workspace,
            utc_now(),
        )
        _atomic_write_json(manifest_path, session.to_dict(self.root))
        return session

    def restore(self, execution_id: str) -> WorkspaceSession:
        """恢复快照会话，位于 Harness Worker 重启后重新连接 Sandbox 的流程环节。"""
        execution_dir = safe_child(self._executions_root, execution_id, "execution_id")
        payload = _read_json(resolve_within(execution_dir, "workspace-snapshot", "manifest.json"))
        source_root = resolve_within(self.root, *str(payload["source_root"]).split("/"))
        snapshot_root = resolve_within(self.root, *str(payload["snapshot_root"]).split("/"))
        workspace_root = resolve_within(self.root, *str(payload["workspace_root"]).split("/"))
        from harness.contracts import WorkspaceConfig

        return WorkspaceSession(
            str(payload["execution_id"]),
            str(payload["attempt_id"]),
            source_root,
            snapshot_root,
            workspace_root,
            str(payload["snapshot_id"]),
            str(payload["workspace_hash"]),
            tuple(WorkspaceFile.from_dict(item) for item in payload.get("files", [])),
            WorkspaceConfig.from_dict(payload.get("config", {})),
            str(payload.get("created_at", "")),
        )

    def create_changeset(self, session: WorkspaceSession) -> ChangeSet:
        """计算并持久化 ChangeSet，位于 Harness Validator 通过后、本地回写前。"""
        baseline = {item.path: item for item in session.files}
        current_files = self._collect_workspace_files(session)
        current = {item.path: item for item in current_files}
        created = tuple(sorted(current.keys() - baseline.keys()))
        deleted = tuple(sorted(baseline.keys() - current.keys()))
        modified = tuple(sorted(
            path for path in current.keys() & baseline.keys()
            if current[path].sha256 != baseline[path].sha256
        ))
        execution_dir = safe_child(self._executions_root, session.execution_id, "execution_id")
        patch_path = resolve_within(execution_dir, "changes.patch")
        patch_text = self._build_patch(session, created, modified, deleted)
        _atomic_write_text(patch_path, patch_text)
        patch_hash = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
        changeset = ChangeSet(
            session.execution_id,
            session.snapshot_id,
            session.workspace_hash,
            created,
            modified,
            deleted,
            relative_to_root(self.root, patch_path),
            patch_hash,
        )
        _atomic_write_json(resolve_within(execution_dir, "changeset.json"), changeset.to_dict())
        return changeset

    def apply(self, session: WorkspaceSession, changeset: ChangeSet) -> ChangeSet:
        """冲突检查后回写本地工程，位于 Harness 验收成功到工程副作用提交的边界。"""
        self._assert_no_conflicts(session, changeset)
        execution_dir = safe_child(self._executions_root, session.execution_id, "execution_id")
        backup_root = resolve_within(execution_dir, "writeback-backup")
        touched = changeset.created + changeset.modified + changeset.deleted
        for relative in changeset.modified + changeset.deleted:
            source = resolve_within(session.source_root, *relative.split("/"))
            backup = resolve_within(backup_root, *relative.split("/"))
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup)
        try:
            for relative in changeset.created + changeset.modified:
                source = resolve_within(session.workspace_root, *relative.split("/"))
                target = resolve_within(session.source_root, *relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.harness-{uuid4().hex[:8]}.tmp")
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
            for relative in changeset.deleted:
                target = resolve_within(session.source_root, *relative.split("/"))
                target.unlink()
        except Exception:
            self._rollback(session, changeset, backup_root)
            raise
        applied = replace(changeset, applied=True, applied_at=utc_now())
        _atomic_write_json(resolve_within(execution_dir, "changeset.json"), applied.to_dict())
        return applied

    def _resolve_source(self, configured: str) -> Path:
        if not configured or "\x00" in configured:
            raise WorkspacePreparationError("Workspace source_root is invalid")
        path = Path(configured)
        if path.is_absolute():
            try:
                resolved = path.resolve()
                resolved.relative_to(self.root)
            except ValueError as exc:
                raise WorkspacePreparationError("Workspace source_root escapes repository") from exc
            return resolved
        parts = [part for part in path.as_posix().split("/") if part not in {"", "."}]
        try:
            return resolve_within(self.root, *parts)
        except ValueError as exc:
            raise WorkspacePreparationError("Workspace source_root escapes repository") from exc

    def _collect_files(self, source_root: Path, task: Task) -> tuple[WorkspaceFile, ...]:
        candidates: Iterable[Path]
        if task.workspace.include_untracked:
            candidates = source_root.rglob("*")
        else:
            try:
                completed = subprocess.run(
                    ["git", "-C", str(source_root), "ls-files", "-z", "--cached"],
                    check=True,
                    capture_output=True,
                    shell=False,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                raise WorkspacePreparationError("Git tracked-file listing failed") from exc
            candidates = (
                resolve_within(source_root, *item.decode("utf-8").split("/"))
                for item in completed.stdout.split(b"\x00") if item
            )
        files: list[WorkspaceFile] = []
        for path in candidates:
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.resolve().relative_to(source_root).as_posix()
            if self._excluded(relative, task.workspace.exclude):
                continue
            files.append(self._describe(path, relative))
        return tuple(sorted(files, key=lambda item: item.path))

    def _collect_workspace_files(self, session: WorkspaceSession) -> tuple[WorkspaceFile, ...]:
        """校验并收集 Agent 输出，位于 ChangeSet 生成前的工作区安全边界。"""
        workspace_root = session.workspace_root.resolve()
        files: list[WorkspaceFile] = []
        total = 0
        for path in workspace_root.rglob("*"):
            if path.is_symlink():
                raise WorkspacePreparationError(f"Workspace symlink is forbidden: {path}")
            if not path.is_file():
                continue
            relative = path.resolve().relative_to(workspace_root).as_posix()
            if self._excluded(relative, session.config.exclude):
                raise WorkspacePreparationError(f"Workspace contains forbidden path: {relative}")
            item = self._describe(path, relative)
            total += item.size_bytes
            if total > self._max_source_bytes:
                raise WorkspacePreparationError(
                    f"Workspace output exceeds limit: {total} > {self._max_source_bytes}"
                )
            files.append(item)
        return tuple(sorted(files, key=lambda item: item.path))

    def _describe(self, path: Path, relative: str) -> WorkspaceFile:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return WorkspaceFile(relative, digest.hexdigest(), size)

    def _excluded(self, relative: str, patterns: tuple[str, ...]) -> bool:
        parts = relative.split("/")
        if any(part in {".git", ".loop", "node_modules", "__pycache__"} for part in parts):
            return True
        if any(part == ".env" or part.startswith(".env.") for part in parts):
            return True
        return any(fnmatch(relative, pattern) or fnmatch(f"/{relative}", pattern) for pattern in patterns)

    def _manifest_hash(self, files: tuple[WorkspaceFile, ...]) -> str:
        encoded = "\n".join(f"{item.path}\0{item.sha256}\0{item.size_bytes}" for item in files)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _build_patch(
        self,
        session: WorkspaceSession,
        created: tuple[str, ...],
        modified: tuple[str, ...],
        deleted: tuple[str, ...],
    ) -> str:
        sections: list[str] = []
        for relative in created + modified + deleted:
            before = resolve_within(session.snapshot_root, *relative.split("/"))
            after = resolve_within(session.workspace_root, *relative.split("/"))
            try:
                before_lines = before.read_text(encoding="utf-8").splitlines(keepends=True) if before.is_file() else []
                after_lines = after.read_text(encoding="utf-8").splitlines(keepends=True) if after.is_file() else []
            except UnicodeDecodeError:
                sections.append(f"Binary files differ: {relative}\n")
                continue
            sections.extend(difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            ))
        return "".join(sections)

    def _assert_no_conflicts(self, session: WorkspaceSession, changeset: ChangeSet) -> None:
        baseline = {item.path: item for item in session.files}
        conflicts: list[str] = []
        for relative in changeset.modified + changeset.deleted:
            target = resolve_within(session.source_root, *relative.split("/"))
            if not target.is_file() or self._describe(target, relative).sha256 != baseline[relative].sha256:
                conflicts.append(relative)
        for relative in changeset.created:
            target = resolve_within(session.source_root, *relative.split("/"))
            if target.exists():
                conflicts.append(relative)
        if conflicts:
            raise WorkspaceConflictError(
                "Local workspace changed during Agent execution: " + ", ".join(sorted(conflicts))
            )

    def _rollback(self, session: WorkspaceSession, changeset: ChangeSet, backup_root: Path) -> None:
        for relative in changeset.modified + changeset.deleted:
            backup = resolve_within(backup_root, *relative.split("/"))
            target = resolve_within(session.source_root, *relative.split("/"))
            if backup.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
        for relative in changeset.created:
            target = resolve_within(session.source_root, *relative.split("/"))
            if target.is_file():
                target.unlink()
