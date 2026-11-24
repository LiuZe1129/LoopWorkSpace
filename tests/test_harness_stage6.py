"""验证 Agent-Harness 阶段 6 的时间线、指标、Golden Cases、版本比较和发布门禁。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.agent_loop import AgentLoop
from harness.contracts import (
    ArtifactRef,
    EvidenceRef,
    Task,
    TaskBudget,
    TaskPermissions,
    TaskResult,
    TaskResultStatus,
    UsageSummary,
)
from harness.controller import HarnessController
from harness.errors import InfrastructureUnavailableError
from harness.evaluation import EvaluationRunner, VersionComparator, load_golden_cases
from harness.integration import build_agent_profile
from harness.models import ModelGateway
from harness.observability import MetricsCollector, TimelineBuilder
from harness.persistence import ResultStore
from harness.sandbox import SandboxManager
from harness.task_runner import HarnessTaskRunner
from harness.tools import PolicyEngine, ToolRouter, build_mvp_registry
from harness.tools.ledger import ToolLedger
from harness.validation import DeterministicValidator
from scripts.check_harness_release_gate import REQUIRED_CASES, REQUIRED_P0, check_release_gate
from tests.test_harness_stage4 import DecisionProvider, LoopSandboxRuntime


class UnavailableRuntime(LoopSandboxRuntime):
    """模拟不可用容器基础设施，仅用于 Harness G-12 离线测试，禁止进入正式入口。"""

    def create(self, spec):
        """拒绝创建 Sandbox，模拟 Harness 正式运行时 Docker 不可用。"""
        raise InfrastructureUnavailableError("Docker CLI is unavailable")


class HarnessStageSixTests(unittest.TestCase):
    """覆盖阶段 6，确认 Harness 能解释、回归比较并安全阻断发布。"""

    def setUp(self) -> None:
        """创建隔离仓库，位于 Harness 可观测性与评测测试准备环节。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        """回收隔离目录，位于 Harness 阶段 6 测试收尾环节。"""
        self.temporary.cleanup()

    def test_timeline_reconstructs_categories_and_redacts_secrets(self) -> None:
        """验证 execution_id 可重建 Harness 时间线且不会泄漏密钥。"""
        controller = HarnessController(self.root)
        task = self._task()
        controller.register_task(task)
        execution, attempt = controller.create_execution(
            task.task_id,
            execution_id="exec-timeline",
            attempt_id="attempt-timeline",
        )
        controller.event_store.append(
            execution.execution_id,
            "model.call",
            attempt_id=attempt.attempt_id,
            payload={"api_key": "sk-super-secret-value", "model": "test-model"},
        )
        controller.event_store.append(
            execution.execution_id,
            "tool.call",
            attempt_id=attempt.attempt_id,
            payload={"status": "denied", "policy": {"action": "deny"}},
        )
        timeline = TimelineBuilder(self.root).build(execution.execution_id)
        self.assertEqual([entry.category for entry in timeline.entries][-2:], ["model", "tool_policy"])
        serialized = str(timeline.to_dict())
        self.assertNotIn("sk-super-secret-value", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_metrics_aggregate_usage_denials_failures_and_recovery(self) -> None:
        """验证 Harness 指标同时覆盖用量、Policy 拒绝、工具失败和恢复。"""
        controller = HarnessController(self.root)
        task = self._task()
        controller.register_task(task)
        execution, attempt = controller.create_execution(
            task.task_id,
            execution_id="exec-metrics",
            attempt_id="attempt-metrics",
        )
        controller.event_store.append(
            execution.execution_id,
            "tool.call",
            attempt_id=attempt.attempt_id,
            payload={"status": "denied", "policy": {"action": "deny"}},
        )
        controller.event_store.append(
            execution.execution_id,
            "execution.resumed",
            attempt_id=attempt.attempt_id,
        )
        result = self._result(execution.execution_id, status=TaskResultStatus.FAILED, failure_kind="policy")
        ResultStore(self.root).write_result(result)
        collector = MetricsCollector(self.root)
        metrics = collector.collect_execution(execution.execution_id)
        self.assertEqual((metrics.policy_denials, metrics.tool_failures, metrics.recoveries), (1, 1, 1))
        report = collector.aggregate([metrics])
        self.assertEqual(report.success_rate, 0.0)
        self.assertEqual(report.failure_kinds, {"policy": 1})

    def test_all_required_golden_cases_are_versioned_and_p0_covered(self) -> None:
        """验证 Harness 最小 Golden Case 集合完整，P0 安全场景进入离线 CI。"""
        cases = load_golden_cases(Path(__file__).parent / "golden" / "cases.json")
        by_id = {case.case_id: case for case in cases}
        self.assertEqual(set(by_id), REQUIRED_CASES)
        self.assertTrue(all(by_id[case_id].priority == "P0" for case_id in REQUIRED_P0))
        self.assertTrue(all(by_id[case_id].mode == "mock" for case_id in REQUIRED_P0))

    def test_evaluation_runner_persists_versions_and_redacts_runner_errors(self) -> None:
        """验证 Harness Evaluation 可重复保存 version_set，并对执行异常脱敏。"""
        cases = load_golden_cases(Path(__file__).parent / "golden" / "cases.json")
        selected = [case for case in cases if case.case_id in {"G-01", "G-02"}]

        def execute(case):
            return self._result(
                f"exec-{case.case_id}",
                status=TaskResultStatus.SUCCEEDED,
                artifacts=1 if case.case_id == "G-02" else 0,
                evidence=2 if case.case_id == "G-02" else 1,
                validation_status="passed",
            )

        report = EvaluationRunner(
            self.root,
            execute,
            harness_version="candidate-1",
        ).run(selected, evaluation_id="eval-stage6")
        self.assertTrue(report.passed)
        self.assertTrue(all("policy" in item.version_set for item in report.outcomes))
        path = self.root / ".loop" / "harness" / "evaluations" / "eval-stage6" / "report.json"
        self.assertTrue(path.is_file())

        def fail_with_secret(case):
            raise RuntimeError("Bearer top-secret-token")

        failed = EvaluationRunner(
            self.root,
            fail_with_secret,
            harness_version="candidate-2",
        ).run(selected[:1], evaluation_id="eval-redacted")
        self.assertNotIn("top-secret-token", str(failed.to_dict()))

    def test_version_comparator_reports_regression_cost_and_latency(self) -> None:
        """验证 Harness 版本比较能识别成功率、成本、延迟和失败原因变化。"""
        cases = load_golden_cases(Path(__file__).parent / "golden" / "cases.json")[:1]
        baseline = EvaluationRunner(
            self.root,
            lambda case: self._result("exec-base", status=TaskResultStatus.SUCCEEDED, evidence=1),
            harness_version="base",
        ).run(cases, evaluation_id="eval-base")
        candidate = EvaluationRunner(
            self.root,
            lambda case: self._result("exec-next", status=TaskResultStatus.FAILED, failure_kind="model"),
            harness_version="next",
        ).run(cases, evaluation_id="eval-next")
        comparison = VersionComparator().compare(baseline, candidate)
        self.assertEqual(comparison["regressions"], ["G-01"])
        self.assertLess(comparison["success_rate_delta"], 0)
        self.assertEqual(comparison["failure_kinds"]["candidate"], {"model": 1})

    def test_release_gate_passes_repository_policy(self) -> None:
        """验证当前仓库满足 Harness P0、无宿主 Shell 和无 Fake 正式入口门禁。"""
        repository_root = Path(__file__).resolve().parents[1]
        self.assertEqual(check_release_gate(repository_root), [])

    def test_task_runner_maps_sandbox_unavailable_without_host_fallback(self) -> None:
        """验证 Sandbox 不可用会形成 infrastructure TaskResult，且 Harness 不回退宿主执行。"""
        controller = HarnessController(self.root)
        sandboxes = SandboxManager(self.root, UnavailableRuntime())
        router = ToolRouter(
            build_mvp_registry(),
            PolicyEngine(),
            ToolLedger(self.root),
            sandboxes,
            platform_tools=frozenset({"read_file", "write_file", "shell_exec"}),
            event_store=controller.event_store,
        )
        profile = build_agent_profile(
            agent_id="unavailable-agent",
            version="1",
            prompt="不可用基础设施测试 Agent",
            model="test-model",
            max_input_tokens=1000,
            max_output_tokens=100,
        )
        task = Task(
            "task-unavailable",
            "run-stage6",
            "node-stage6",
            "验证基础设施失败",
            profile.to_ref(),
            TaskPermissions(()),
            TaskBudget(),
        )
        loop = AgentLoop(
            controller,
            ModelGateway(DecisionProvider([]), max_attempts=1),
            router,
        )
        result = HarnessTaskRunner(
            self.root,
            controller,
            loop,
            DeterministicValidator(router),
            sandboxes,
        ).run(task, profile)
        self.assertEqual(result.status, TaskResultStatus.FAILED)
        self.assertEqual(result.failure_kind, "infrastructure")
        self.assertEqual(result.error_code, "infrastructure_unavailable")

    def _task(self) -> Task:
        profile = build_agent_profile(
            agent_id="stage6-agent",
            version="1",
            prompt="阶段六测试 Agent",
            model="test-model",
            max_input_tokens=1000,
            max_output_tokens=100,
        )
        return Task(
            "task-stage6",
            "run-stage6",
            "node-stage6",
            "验证可观测性",
            profile.to_ref(),
            TaskPermissions(()),
            TaskBudget(),
        )

    def _result(
        self,
        execution_id: str,
        *,
        status: TaskResultStatus,
        failure_kind: str | None = None,
        artifacts: int = 0,
        evidence: int = 0,
        validation_status: str = "passed",
    ) -> TaskResult:
        artifact_refs = tuple(
            ArtifactRef("sandbox-file", f"artifact-{index}.txt", f"hash-{index}", 1)
            for index in range(artifacts)
        )
        evidence_refs = tuple(
            EvidenceRef("validation-report", f"evidence-{index}.json", f"hash-{index}", "验收")
            for index in range(evidence)
        )
        return TaskResult(
            "task-stage6",
            execution_id,
            "attempt-stage6",
            status,
            "stage6 result",
            UsageSummary(1, 1, 10, 0.01, 1.0),
            artifacts=artifact_refs,
            evidence=evidence_refs,
            validation={"status": validation_status},
            version_set={
                "agent_profile": "stage6-agent@1",
                "model": "test-model",
                "policy": "mvp-policy-v1",
                "runtime": "FakeSandboxManager",
            },
            failure_kind=failure_kind,
        )
