"""Agent-Harness 执行与评测指标聚合器。

该模块位于 TaskResult/EventStore 与发布评审之间，计算成功率、资源用量、延迟、
拒绝、工具失败和恢复次数；指标只保留数字与稳定分类，不收集敏感正文。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from harness.contracts import TaskResult, TaskResultStatus
from harness.persistence import EventStore, ResultStore


@dataclass(frozen=True)
class ExecutionMetrics:
    """描述单次执行指标，位于 Harness TaskResult 到聚合报告的转换边界。"""

    execution_id: str
    status: str
    rounds: int
    tool_calls: int
    model_tokens: int
    cost: float | None
    wall_seconds: float
    policy_denials: int
    tool_failures: int
    recoveries: int
    failure_kind: str | None

    def to_dict(self) -> dict[str, Any]:
        """序列化单次指标，供 Harness Evaluation 与版本对比使用。"""
        return {
            "execution_id": self.execution_id,
            "status": self.status,
            "rounds": self.rounds,
            "tool_calls": self.tool_calls,
            "model_tokens": self.model_tokens,
            "cost": self.cost,
            "wall_seconds": self.wall_seconds,
            "policy_denials": self.policy_denials,
            "tool_failures": self.tool_failures,
            "recoveries": self.recoveries,
            "failure_kind": self.failure_kind,
        }


@dataclass(frozen=True)
class MetricsReport:
    """汇总多个 Execution 指标，位于 Harness 回归结果与发布门禁之间。"""

    executions: int
    successes: int
    success_rate: float
    total_rounds: int
    total_tool_calls: int
    total_model_tokens: int
    total_cost: float | None
    average_wall_seconds: float
    policy_denials: int
    tool_failures: int
    recoveries: int
    failure_kinds: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        """序列化聚合指标，供 Harness CI 报告和版本比较持久化。"""
        return {
            "executions": self.executions,
            "successes": self.successes,
            "success_rate": self.success_rate,
            "total_rounds": self.total_rounds,
            "total_tool_calls": self.total_tool_calls,
            "total_model_tokens": self.total_model_tokens,
            "total_cost": self.total_cost,
            "average_wall_seconds": self.average_wall_seconds,
            "policy_denials": self.policy_denials,
            "tool_failures": self.tool_failures,
            "recoveries": self.recoveries,
            "failure_kinds": dict(self.failure_kinds),
        }


class MetricsCollector:
    """从结果和事件计算指标，位于 Harness Observability 的只读聚合层。"""

    def __init__(self, root: Path):
        self._results = ResultStore(root)
        self._events = EventStore(root)

    def collect_execution(self, execution_id: str) -> ExecutionMetrics:
        """计算单 Execution 指标，位于 Harness 任务结束后的评测采集环节。"""
        result = self._results.load_result(execution_id)
        return self.from_result(result)

    def from_result(self, result: TaskResult) -> ExecutionMetrics:
        """结合 TaskResult 与事件生成指标，位于 Harness 结果契约到统计视图的边界。"""
        events = self._events.read(result.execution_id)
        policy_denials = sum(
            1 for event in events
            if event.event_type == "tool.call"
            and (event.payload.get("policy") or {}).get("action") == "deny"
        )
        tool_failures = sum(
            1 for event in events
            if event.event_type == "tool.call" and event.payload.get("status") != "succeeded"
        )
        recoveries = sum(
            1 for event in events
            if event.event_type in {"execution.resumed", "attempt.created"}
        )
        return ExecutionMetrics(
            result.execution_id,
            result.status.value,
            result.usage.rounds,
            result.usage.tool_calls,
            result.usage.model_tokens,
            result.usage.cost,
            result.usage.wall_seconds,
            policy_denials,
            tool_failures,
            recoveries,
            result.failure_kind,
        )

    def aggregate(self, metrics: Iterable[ExecutionMetrics]) -> MetricsReport:
        """聚合执行集合，位于 Harness Golden Cases 与发布阈值判定之前。"""
        values = list(metrics)
        successes = sum(1 for item in values if item.status == TaskResultStatus.SUCCEEDED.value)
        known_costs = [item.cost for item in values if item.cost is not None]
        failure_kinds: dict[str, int] = {}
        for item in values:
            if item.failure_kind:
                failure_kinds[item.failure_kind] = failure_kinds.get(item.failure_kind, 0) + 1
        count = len(values)
        return MetricsReport(
            count,
            successes,
            successes / count if count else 0.0,
            sum(item.rounds for item in values),
            sum(item.tool_calls for item in values),
            sum(item.model_tokens for item in values),
            sum(known_costs) if len(known_costs) == count else None,
            sum(item.wall_seconds for item in values) / count if count else 0.0,
            sum(item.policy_denials for item in values),
            sum(item.tool_failures for item in values),
            sum(item.recoveries for item in values),
            failure_kinds,
        )
