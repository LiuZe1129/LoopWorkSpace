"""Agent-Harness 原子预算判定组件。

该模块位于 Agent Loop 每轮、模型响应后和 ToolCall 前后，依据持久化 Attempt 用量与
Task 上限检查轮次、Tool、Token、费用和墙钟时间，重启后继续沿用原消耗。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .contracts import Attempt, Task
from .errors import BudgetExceededError


class BudgetMeter:
    """检查 Task 资源预算，位于 Harness Agent Loop 所有外部调用的控制边界。"""

    def __init__(self, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def check(
        self,
        task: Task,
        attempt: Attempt,
        *,
        next_rounds: int = 0,
        next_tool_calls: int = 0,
        additional_tokens: int = 0,
        additional_cost: float | None = 0.0,
    ) -> None:
        """拒绝将超出上限的动作，位于 Harness 模型与 Tool 执行前后的预算门禁。"""
        if attempt.rounds_used + next_rounds > task.budget.max_rounds:
            raise BudgetExceededError("rounds", "Agent round budget exhausted")
        if attempt.tool_calls_used + next_tool_calls > task.budget.max_tool_calls:
            raise BudgetExceededError("tool_calls", "ToolCall budget exhausted")
        if attempt.model_tokens_used + additional_tokens > task.budget.max_model_tokens:
            raise BudgetExceededError("model_tokens", "Model token budget exhausted")
        if task.budget.max_cost is not None:
            if attempt.cost_used is None or additional_cost is None:
                raise BudgetExceededError("cost_unknown", "Model cost is unknown while Task has a cost limit")
            if attempt.cost_used + additional_cost > task.budget.max_cost:
                raise BudgetExceededError("cost", "Model cost budget exhausted")
        created_at = datetime.fromisoformat(attempt.created_at)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if (now - created_at).total_seconds() >= task.budget.max_wall_seconds:
            raise BudgetExceededError("wall_seconds", "Task wall-clock budget exhausted")

    def wall_seconds(self, attempt: Attempt) -> float:
        """计算累计墙钟时间，位于 Harness TaskResult 与预算事件生成的统计环节。"""
        created_at = datetime.fromisoformat(attempt.created_at)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return max(0.0, (now - created_at).total_seconds())
