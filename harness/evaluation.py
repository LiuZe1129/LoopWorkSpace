"""Agent-Harness Golden Case 评测与版本对比。

该模块位于可注入的 Task 执行器与 CI 发布门禁之间，加载版本化用例、执行断言、
保存脱敏报告并比较两个 Harness 版本的成功率、成本、延迟和失败分类。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from scheduler.path_security import resolve_within, safe_child

from .contracts import TaskResult, utc_now
from .observability.redaction import redact_value
from .persistence.atomic import _atomic_write_json


@dataclass(frozen=True)
class GoldenCase:
    """描述可重复评测场景，位于 Harness 测试输入与执行器之间的版本化边界。"""

    case_id: str
    description: str
    priority: str
    mode: str
    task: dict[str, Any]
    environment: dict[str, Any]
    expected: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GoldenCase":
        """校验并恢复 Golden Case，位于 Harness Evaluation 加载外部用例的入口。"""
        required = {"case_id", "description", "priority", "mode", "task", "environment", "expected"}
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError(f"Golden Case missing fields: {', '.join(missing)}")
        case_id = str(payload["case_id"])
        if not case_id.startswith("G-"):
            raise ValueError("Golden Case case_id must start with G-")
        mode = str(payload["mode"])
        if mode not in {"mock", "live"}:
            raise ValueError("Golden Case mode must be mock or live")
        priority = str(payload["priority"])
        if priority not in {"P0", "P1", "P2"}:
            raise ValueError("Golden Case priority must be P0, P1 or P2")
        return cls(
            case_id,
            str(payload["description"]),
            priority,
            mode,
            dict(payload["task"]),
            dict(payload["environment"]),
            dict(payload["expected"]),
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化用例，供 Harness 评测报告记录输入版本而不记录运行时秘密。"""
        return {
            "schema_version": "1.0",
            "case_id": self.case_id,
            "description": self.description,
            "priority": self.priority,
            "mode": self.mode,
            "task": dict(self.task),
            "environment": dict(self.environment),
            "expected": dict(self.expected),
        }


@dataclass(frozen=True)
class CaseOutcome:
    """描述单个 Golden Case 结论，位于 Harness 执行结果与评测报告之间。"""

    case_id: str
    passed: bool
    result_status: str
    failure_kind: str | None
    assertions: tuple[str, ...]
    usage: dict[str, Any]
    version_set: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        """序列化脱敏用例结论，供 Harness CI 和版本比较消费。"""
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "result_status": self.result_status,
            "failure_kind": self.failure_kind,
            "assertions": list(self.assertions),
            "usage": dict(self.usage),
            "version_set": dict(self.version_set),
        }


@dataclass(frozen=True)
class EvaluationReport:
    """封装一轮 Golden Case 评测，位于 Harness 回归执行与发布门禁之间。"""

    evaluation_id: str
    created_at: str
    harness_version: str
    outcomes: tuple[CaseOutcome, ...]

    @property
    def passed(self) -> bool:
        """判断整轮评测是否通过，位于 Harness CI 发布决策的布尔门禁。"""
        return bool(self.outcomes) and all(outcome.passed for outcome in self.outcomes)

    def to_dict(self) -> dict[str, Any]:
        """序列化评测报告，供 Harness 版本归档和 CI Artifact 使用。"""
        passed_count = sum(1 for outcome in self.outcomes if outcome.passed)
        costs = [outcome.usage.get("cost") for outcome in self.outcomes]
        known_cost = all(value is not None for value in costs)
        return {
            "schema_version": "1.0",
            "evaluation_id": self.evaluation_id,
            "created_at": self.created_at,
            "harness_version": self.harness_version,
            "passed": self.passed,
            "summary": {
                "cases": len(self.outcomes),
                "passed": passed_count,
                "success_rate": passed_count / len(self.outcomes) if self.outcomes else 0.0,
                "total_cost": sum(float(value) for value in costs) if known_cost else None,
                "average_wall_seconds": (
                    sum(float(outcome.usage.get("wall_seconds", 0.0)) for outcome in self.outcomes)
                    / len(self.outcomes)
                    if self.outcomes else 0.0
                ),
            },
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvaluationReport":
        """恢复评测报告，位于 Harness 历史版本比较的读取环节。"""
        outcomes = tuple(
            CaseOutcome(
                str(item["case_id"]),
                bool(item["passed"]),
                str(item["result_status"]),
                item.get("failure_kind"),
                tuple(str(value) for value in item.get("assertions", []) or []),
                dict(item.get("usage", {}) or {}),
                {str(key): str(value) for key, value in (item.get("version_set", {}) or {}).items()},
            )
            for item in payload.get("outcomes", []) or []
        )
        return cls(
            str(payload["evaluation_id"]),
            str(payload["created_at"]),
            str(payload["harness_version"]),
            outcomes,
        )


def load_golden_cases(path: Path) -> tuple[GoldenCase, ...]:
    """加载并检查唯一用例 ID，位于 Harness Golden Case 文件到 Runner 的输入边界。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Golden Case file must contain a non-empty cases array")
    cases = tuple(GoldenCase.from_dict(dict(item)) for item in raw_cases)
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Golden Case IDs must be unique")
    return cases


class EvaluationRunner:
    """批量执行 Golden Cases，位于 Harness 可注入执行器与版本化评测报告之间。"""

    def __init__(
        self,
        root: Path,
        executor: Callable[[GoldenCase], TaskResult],
        *,
        harness_version: str,
    ) -> None:
        self.root = root.resolve()
        self._executor = executor
        self._harness_version = harness_version

    def run(
        self,
        cases: Iterable[GoldenCase],
        *,
        evaluation_id: str | None = None,
    ) -> EvaluationReport:
        """执行用例并保存脱敏报告，位于 Harness 回归运行与 CI 判定环节。"""
        selected_id = evaluation_id or f"eval-{uuid4().hex}"
        outcomes: list[CaseOutcome] = []
        for case in cases:
            try:
                result = self._executor(case)
                assertions = tuple(self._assert_result(case, result))
                outcomes.append(CaseOutcome(
                    case.case_id,
                    not assertions,
                    result.status.value,
                    result.failure_kind,
                    assertions,
                    result.usage.to_dict(),
                    dict(result.version_set),
                ))
            except Exception as exc:
                safe = redact_value({"error": str(exc)})["error"]
                outcomes.append(CaseOutcome(
                    case.case_id,
                    False,
                    "runner_error",
                    "infrastructure",
                    (str(safe),),
                    {},
                    {},
                ))
        report = EvaluationReport(
            selected_id,
            utc_now(),
            self._harness_version,
            tuple(outcomes),
        )
        directory = safe_child(
            resolve_within(self.root, ".loop", "harness", "evaluations"),
            selected_id,
            "evaluation_id",
        )
        _atomic_write_json(resolve_within(directory, "report.json"), report.to_dict())
        return report

    def _assert_result(self, case: GoldenCase, result: TaskResult) -> list[str]:
        expected = case.expected
        failures: list[str] = []
        if expected.get("status") and result.status.value != expected["status"]:
            failures.append(f"status expected {expected['status']} but got {result.status.value}")
        if "failure_kind" in expected and result.failure_kind != expected["failure_kind"]:
            failures.append(f"failure_kind expected {expected['failure_kind']} but got {result.failure_kind}")
        if len(result.artifacts) < int(expected.get("min_artifacts", 0)):
            failures.append("artifact count is below expectation")
        if len(result.evidence) < int(expected.get("min_evidence", 0)):
            failures.append("evidence count is below expectation")
        if expected.get("validation_status") and result.validation.get("status") != expected["validation_status"]:
            failures.append("validation status does not match")
        for key in expected.get("required_version_keys", []) or []:
            if str(key) not in result.version_set:
                failures.append(f"version_set is missing {key}")
        limits = {
            "max_rounds": result.usage.rounds,
            "max_tool_calls": result.usage.tool_calls,
            "max_model_tokens": result.usage.model_tokens,
            "max_wall_seconds": result.usage.wall_seconds,
        }
        for name, actual in limits.items():
            if name in expected and actual > float(expected[name]):
                failures.append(f"{name} exceeded: {actual}")
        return failures


class VersionComparator:
    """比较两轮评测，位于 Harness Prompt/Model/Policy/Runtime 变更评审环节。"""

    def compare(self, baseline: EvaluationReport, candidate: EvaluationReport) -> dict[str, Any]:
        """计算成功率、成本、延迟和失败原因差异，供 Harness 发布者判断回归。"""
        base = baseline.to_dict()["summary"]
        cand = candidate.to_dict()["summary"]
        baseline_cases = {item.case_id: item for item in baseline.outcomes}
        candidate_cases = {item.case_id: item for item in candidate.outcomes}
        regressions = sorted(
            case_id for case_id, item in baseline_cases.items()
            if item.passed and case_id in candidate_cases and not candidate_cases[case_id].passed
        )
        improvements = sorted(
            case_id for case_id, item in baseline_cases.items()
            if not item.passed and case_id in candidate_cases and candidate_cases[case_id].passed
        )
        return {
            "schema_version": "1.0",
            "baseline_version": baseline.harness_version,
            "candidate_version": candidate.harness_version,
            "success_rate_delta": cand["success_rate"] - base["success_rate"],
            "total_cost_delta": self._delta(base["total_cost"], cand["total_cost"]),
            "average_wall_seconds_delta": cand["average_wall_seconds"] - base["average_wall_seconds"],
            "regressions": regressions,
            "improvements": improvements,
            "failure_kinds": {
                "baseline": self._failure_kinds(baseline.outcomes),
                "candidate": self._failure_kinds(candidate.outcomes),
            },
        }

    @staticmethod
    def _delta(baseline: float | None, candidate: float | None) -> float | None:
        if baseline is None or candidate is None:
            return None
        return float(candidate) - float(baseline)

    @staticmethod
    def _failure_kinds(outcomes: tuple[CaseOutcome, ...]) -> dict[str, int]:
        values: dict[str, int] = {}
        for outcome in outcomes:
            if outcome.failure_kind:
                values[outcome.failure_kind] = values.get(outcome.failure_kind, 0) + 1
        return values
