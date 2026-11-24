"""Agent-Harness Task、TaskExecution 与 Attempt 文件存储。

该模块位于 Harness Controller 的状态写入环节，使用原子 JSON 与 state_version
compare-and-swap，确保单 Task 在并发 Worker 或进程异常时不会静默覆盖状态。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from scheduler.path_security import resolve_within, safe_child

from harness.contracts import Attempt, Task, TaskExecution, utc_now
from harness.errors import AlreadyExistsError, NotFoundError, VersionConflictError

from .atomic import _atomic_write_json, _exclusive_lock, _read_json


class TaskStore:
    """持久化 Harness 领域对象，位于 Controller 与磁盘状态之间的仓储边界。"""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.harness_root = resolve_within(self.root, ".loop", "harness")
        self.tasks_dir = resolve_within(self.harness_root, "tasks")
        self.executions_dir = resolve_within(self.harness_root, "executions")

    def create_task(self, task: Task) -> Task:
        """首次写入不可变 Task，位于 GraphFlow 提交任务到 Harness 的入口环节。"""
        path = self._task_path(task.task_id)
        self._create_record(path, task.to_dict(), "Task")
        return task

    def load_task(self, task_id: str) -> Task:
        """加载 Task Contract，位于 Harness 创建或恢复 Execution 的输入装配环节。"""
        path = self._task_path(task_id)
        if not path.is_file():
            raise NotFoundError(f"Task not found: {task_id}")
        return Task.from_dict(_read_json(path))

    def create_execution(self, execution: TaskExecution) -> TaskExecution:
        """首次写入 TaskExecution，位于 Harness Controller 创建运行实例的环节。"""
        path = self._execution_path(execution.execution_id)
        self._create_record(path, execution.to_dict(), "TaskExecution")
        return execution

    def load_execution(self, execution_id: str) -> TaskExecution:
        """加载 TaskExecution，位于 Harness Worker 领取任务和恢复状态的环节。"""
        path = self._execution_path(execution_id)
        if not path.is_file():
            raise NotFoundError(f"TaskExecution not found: {execution_id}")
        return TaskExecution.from_dict(_read_json(path))

    def save_execution(self, execution: TaskExecution, *, expected_version: int) -> TaskExecution:
        """使用 CAS 更新 Execution，位于 Harness 状态机确认合法转换后的持久化环节。"""
        path = self._execution_path(execution.execution_id)
        with _exclusive_lock(path):
            if not path.is_file():
                raise NotFoundError(f"TaskExecution not found: {execution.execution_id}")
            current = TaskExecution.from_dict(_read_json(path))
            if current.state_version != expected_version:
                raise VersionConflictError(
                    f"TaskExecution version conflict: expected {expected_version}, actual {current.state_version}"
                )
            saved = replace(execution, state_version=expected_version + 1, updated_at=utc_now())
            _atomic_write_json(path, saved.to_dict())
            return saved

    def create_attempt(self, attempt: Attempt) -> Attempt:
        """首次写入独立 Attempt，位于 Harness 每次新尝试开始前的持久化环节。"""
        path = self._attempt_path(attempt.execution_id, attempt.attempt_id)
        self._create_record(path, attempt.to_dict(), "Attempt")
        return attempt

    def load_attempt(self, execution_id: str, attempt_id: str) -> Attempt:
        """加载 Attempt，位于 Harness Worker 恢复 Sandbox 与执行进度的环节。"""
        path = self._attempt_path(execution_id, attempt_id)
        if not path.is_file():
            raise NotFoundError(f"Attempt not found: {attempt_id}")
        return Attempt.from_dict(_read_json(path))

    def save_attempt(self, attempt: Attempt, *, expected_version: int) -> Attempt:
        """使用 CAS 更新 Attempt，位于 Harness Tool/Checkpoint 状态落盘的环节。"""
        path = self._attempt_path(attempt.execution_id, attempt.attempt_id)
        with _exclusive_lock(path):
            if not path.is_file():
                raise NotFoundError(f"Attempt not found: {attempt.attempt_id}")
            current = Attempt.from_dict(_read_json(path))
            if current.state_version != expected_version:
                raise VersionConflictError(
                    f"Attempt version conflict: expected {expected_version}, actual {current.state_version}"
                )
            saved = replace(attempt, state_version=expected_version + 1, updated_at=utc_now())
            _atomic_write_json(path, saved.to_dict())
            return saved

    def list_attempts(self, execution_id: str) -> list[Attempt]:
        """按序列列出 Attempt，位于 Harness 重试决策和审计时间线组装环节。"""
        attempts_dir = self._attempts_dir(execution_id)
        if not attempts_dir.is_dir():
            return []
        attempts = [Attempt.from_dict(_read_json(path)) for path in attempts_dir.glob("*.json")]
        return sorted(attempts, key=lambda item: item.sequence)

    def list_executions(self) -> list[TaskExecution]:
        """列出 Execution，位于 Harness Reconciler 服务启动和定时扫描流程。"""
        if not self.executions_dir.is_dir():
            return []
        executions: list[TaskExecution] = []
        for directory in self.executions_dir.iterdir():
            path = directory / "execution.json"
            if path.is_file():
                executions.append(TaskExecution.from_dict(_read_json(path)))
        return sorted(executions, key=lambda item: item.created_at)

    def _task_path(self, task_id: str) -> Path:
        return resolve_within(safe_child(self.tasks_dir, task_id, "task_id"), "task.json")

    def _execution_dir(self, execution_id: str) -> Path:
        return safe_child(self.executions_dir, execution_id, "execution_id")

    def _execution_path(self, execution_id: str) -> Path:
        return resolve_within(self._execution_dir(execution_id), "execution.json")

    def _attempts_dir(self, execution_id: str) -> Path:
        return resolve_within(self._execution_dir(execution_id), "attempts")

    def _attempt_path(self, execution_id: str, attempt_id: str) -> Path:
        return safe_child(self._attempts_dir(execution_id), f"{attempt_id}.json", "attempt filename")

    def _create_record(self, path: Path, payload: dict, resource_name: str) -> None:
        with _exclusive_lock(path):
            if path.exists():
                raise AlreadyExistsError(f"{resource_name} already exists: {path}")
            _atomic_write_json(path, payload)
