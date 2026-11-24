"""Agent-Harness 文件型存储的原子操作。

该模块位于领域 Store 与磁盘之间，使用同目录临时文件、fsync、原子替换和短期锁，
避免 TaskExecution 状态更新过程中产生部分 JSON 或并发覆盖。
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from harness.errors import VersionConflictError


@contextmanager
def _exclusive_lock(target: Path, *, timeout_seconds: float = 5.0) -> Iterator[None]:
    lock_path = target.with_name(f".{target.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Harness 使用 OS 文件锁而不是“锁文件是否存在”作为所有权依据；进程崩溃后内核会自动释放。
    handle = lock_path.open("a+b")
    if lock_path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    started = time.monotonic()
    while True:
        try:
            _lock_file(handle)
            break
        except (BlockingIOError, OSError) as exc:
            if time.monotonic() - started >= timeout_seconds:
                handle.close()
                raise VersionConflictError(f"record lock timed out: {target}") from exc
            time.sleep(0.05)
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())
        yield
    finally:
        try:
            _unlock_file(handle)
        finally:
            handle.close()


def _lock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件只需在同目录内唯一；避免重复长目标名导致 Windows Harness 路径超过限制。
    temporary = path.with_name(f".tmp-{uuid4().hex[:12]}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 与 JSON 写入保持同目录原子替换，同时控制 Harness 持久化路径长度。
    temporary = path.with_name(f".tmp-{uuid4().hex[:12]}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _append_json_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
