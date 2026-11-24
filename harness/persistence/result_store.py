"""Agent-Harness TaskResult 与 ValidationReport 存储。

该模块位于 Validator/Runner 与 GraphFlow Handler 之间，以原子 JSON 保存最终结果和确定性
验证报告，使 GraphFlow 只读取稳定引用而不依赖 Agent 内部消息。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scheduler.path_security import resolve_within, safe_child

from harness.contracts import TaskResult
from harness.errors import NotFoundError

from .atomic import _atomic_write_json, _read_json


class ResultStore:
    """持久化 Harness 结果与验证证据，位于执行内核和 GraphFlow 的存储边界。"""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.executions_dir = resolve_within(self.root, ".loop", "harness", "executions")

    def write_validation(self, execution_id: str, payload: dict[str, Any]) -> Path:
        """写入验证报告，位于 Harness Validator 完成确定性检查后的提交环节。"""
        path = resolve_within(self._execution_dir(execution_id), "validation.json")
        _atomic_write_json(path, payload)
        return path

    def write_result(self, result: TaskResult) -> Path:
        """写入最终 TaskResult，位于 Harness Execution 进入终态后的结果提交环节。"""
        path = resolve_within(self._execution_dir(result.execution_id), "result.json")
        _atomic_write_json(path, result.to_dict())
        return path

    def load_result(self, execution_id: str) -> TaskResult:
        """加载最终 TaskResult，位于 Harness GraphFlow Handler 或 CLI 查询结果的环节。"""
        path = resolve_within(self._execution_dir(execution_id), "result.json")
        if not path.is_file():
            raise NotFoundError(f"TaskResult not found: {execution_id}")
        return TaskResult.from_dict(_read_json(path))

    def _execution_dir(self, execution_id: str) -> Path:
        return safe_child(self.executions_dir, execution_id, "execution_id")
