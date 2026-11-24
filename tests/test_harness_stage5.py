"""验证 Agent-Harness 阶段 5 的确定性验收、TaskResult 与 GraphFlow 集成。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness.agent_loop import AgentLoop
from harness.contracts import (
    EvidenceRef,
    Task,
    TaskBudget,
    TaskPermissions,
    TaskResult,
    TaskResultStatus,
    UsageSummary,
)
from harness.controller import HarnessController
from harness.integration import build_agent_profile
from harness.integration.graphflow_handler import HarnessNodeHandler
from harness.models import ModelGateway
from harness.persistence import ResultStore
from harness.sandbox import SandboxManager
from harness.task_runner import HarnessTaskRunner
from harness.tools import PolicyEngine, ToolRouter, build_mvp_registry
from harness.tools.ledger import ToolLedger
from harness.validation import DeterministicValidator
from scheduler.executor import DRGExecutor, NodeExecutionResult
from scheduler.gate_auto import build_harness_acceptance
from scheduler.retry_policy import RetryPolicy
from scheduler.runs import RunState
from scheduler.specs import SpecRef
from tests.test_harness_stage4 import DecisionProvider, LoopSandboxRuntime


class FakeTaskRunner:
    """返回预置 TaskResult，模拟 GraphFlow 到 Harness 的稳定结果边界。"""

    def __init__(self, result: TaskResult):
        self.result = result
        self.seen_task: Task | None = None

    def run(self, task: Task, profile) -> TaskResult:
        """记录转换后的 Task 并返回结果，用于验证 GraphFlow 适配流程。"""
        self.seen_task = task
        return self.result


class HarnessStageFiveTests(unittest.TestCase):
    """覆盖阶段 5，确认模型完成声明必须经过 Validator 才能成功。"""

    def setUp(self) -> None:
        """装配隔离 Harness 组件，位于阶段 5 集成测试准备环节。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.controller = HarnessController(self.root)
        self.runtime = LoopSandboxRuntime()
        self.sandboxes = SandboxManager(self.root, self.runtime)
        self.ledger = ToolLedger(self.root)

    def tearDown(self) -> None:
        """回收隔离目录，位于阶段 5 Harness 测试收尾环节。"""
        self.temporary.cleanup()

    def profile(self, *, agent_id: str = "backend-dev"):
        """构造带输出 Schema 的 Profile，位于 Harness 验收输入边界。"""
        return build_agent_profile(
            agent_id=agent_id,
            version="1.0.0",
            prompt="执行单任务并输出结构化决策",
            model="test-model",
            max_input_tokens=4000,
            max_output_tokens=200,
            tools=("read_file", "write_file", "shell_exec"),
            output_schema={
                "type": "object",
                "required": ["summary"],
                "properties": {"summary": {"type": "string"}},
                "additionalProperties": False,
            },
        )

    def runner(self, decisions: list[dict]) -> HarnessTaskRunner:
        """装配真实 Loop/Validator/ResultStore，位于 Harness 端到端测试入口。"""
        router = ToolRouter(
            build_mvp_registry(),
            PolicyEngine(),
            self.ledger,
            self.sandboxes,
            platform_tools=frozenset({"read_file", "write_file", "shell_exec"}),
            event_store=self.controller.event_store,
        )
        loop = AgentLoop(
            self.controller,
            ModelGateway(DecisionProvider(decisions), max_attempts=1, sleeper=lambda _: None),
            router,
        )
        return HarnessTaskRunner(
            self.root,
            self.controller,
            loop,
            DeterministicValidator(router),
            self.sandboxes,
        )

    def task(self, profile, *, acceptance: tuple[dict, ...]) -> Task:
        """构造带确定性 acceptance 的 Task，位于 GraphFlow 到 Harness 的契约边界。"""
        return Task(
            task_id="task-stage5",
            graph_run_id="run-stage5",
            node_id="implement-node",
            objective="生成并验证结果文件",
            agent_profile=profile.to_ref(),
            permissions=TaskPermissions(("read_file", "write_file", "shell_exec")),
            budget=TaskBudget(max_rounds=5, max_tool_calls=5, max_model_tokens=1000),
            acceptance=acceptance,
        )

    def test_completion_claim_fails_when_file_acceptance_fails(self) -> None:
        """验证模型声称完成但文件不存在时 Harness 不得产生成功结果。"""
        profile = self.profile()
        task = self.task(profile, acceptance=({"type": "file_exists", "path": "missing.txt"},))
        result = self.runner([
            {"action": "complete", "result": {"summary": "claimed done"}},
        ]).run(task, profile)
        self.assertEqual(result.status, TaskResultStatus.FAILED)
        self.assertEqual(result.failure_kind, "validation")
        self.assertEqual(result.validation["status"], "failed")
        execution = self.controller.task_store.load_execution(result.execution_id)
        self.assertEqual(execution.status.value, "FAILED")

    def test_validated_task_persists_artifacts_evidence_and_result_ref(self) -> None:
        """验证成功 Task 必须具备产物、Evidence 与 Execution 结果引用。"""
        profile = self.profile()
        task = self.task(profile, acceptance=(
            {"type": "file_contains", "path": "result.txt", "text": "done"},
            {"type": "command", "argv": ["echo", "verified"], "expected_exit_code": 0},
        ))
        result = self.runner([
            {
                "action": "tool_call",
                "tool_name": "write_file",
                "arguments": {"path": "result.txt", "content": "done"},
                "idempotency_key": "write-result",
            },
            {"action": "complete", "result": {"summary": "validated"}},
        ]).run(task, profile)
        self.assertEqual(result.status, TaskResultStatus.SUCCEEDED)
        self.assertTrue(result.artifacts)
        self.assertGreaterEqual(len(result.evidence), 2)
        self.assertEqual(result.validation["status"], "passed")
        persisted = ResultStore(self.root).load_result(result.execution_id)
        self.assertEqual(persisted.to_dict(), result.to_dict())
        execution = self.controller.task_store.load_execution(result.execution_id)
        self.assertEqual(execution.status.value, "SUCCEEDED")
        self.assertEqual(
            execution.result_ref,
            f".loop/harness/executions/{result.execution_id}/result.json",
        )

    def test_graphflow_handler_writes_stable_boundary_artifact(self) -> None:
        """验证 GraphFlow 仅消费 TaskResult，不依赖 Agent 消息与 ToolCall 内部结构。"""
        profile = self.profile()
        result = TaskResult(
            task_id="task-result",
            execution_id="exec-result",
            attempt_id="attempt-result",
            status=TaskResultStatus.SUCCEEDED,
            summary="completed",
            usage=UsageSummary(1, 1, 10, 0.01, 1.0),
            evidence=(EvidenceRef("validation-report", "evidence.json", "abc", "验收"),),
            validation={"status": "passed"},
        )
        runner = FakeTaskRunner(result)
        handler = HarnessNodeHandler(self.root, runner, lambda _: profile)
        state = self._run_state()
        node = {
            "id": "implementation-1",
            "type": "implementation",
            "agent": "backend-dev",
            "context_pack_path": ".loop/runs/run-1/context.json",
            "harness_acceptance": [{"type": "file_exists", "path": "output.txt"}],
        }
        mapped = handler("run-1", {"schema_version": "1.0"}, state, node)
        self.assertEqual(mapped.status, "success")
        self.assertIsNotNone(runner.seen_task)
        artifact = json.loads((self.root / mapped.artifacts[0]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(artifact["task_id"], "task-result")
        self.assertEqual(artifact["execution_id"], "exec-result")
        self.assertIn("result_ref", artifact)
        self.assertNotIn("messages", artifact)
        self.assertNotIn("tool_calls", artifact)

    def test_executor_routes_implementation_test_and_gate_to_harness(self) -> None:
        """验证正式模式的关键节点统一进入 Harness Handler，而非宿主机执行。"""
        seen: list[str] = []

        def handler(run_id, graph, state, node):
            seen.append(node["type"])
            return NodeExecutionResult("success", "handled")

        executor = DRGExecutor(self.root, harness_handler=handler, execution_mode="harness")
        state = self._run_state()
        graph = {"nodes": []}
        for node_type in ("implementation", "test", "gate"):
            result = executor.execute_node(
                "run-1",
                graph,
                state,
                {"id": f"{node_type}-1", "type": node_type, "agent": "backend-dev"},
            )
            self.assertEqual(result.status, "success")
        self.assertEqual(seen, ["implementation", "test", "gate"])

    def test_gate_rules_convert_to_sandbox_argv_without_host_shell(self) -> None:
        """验证旧 Gate 命令被转换为 Harness argv，Shell 运算符不会进入正式执行路径。"""
        snapshot = self.root / "specs" / "spec-1.json"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(json.dumps({
            "acceptance_criteria": [
                {"id": "ACC-1", "check": "python -m pytest", "expect": "exitcode:0"},
                {"id": "ACC-2", "check": "echo ok && echo unsafe", "expect": "contains:ok"},
            ],
        }), encoding="utf-8")
        converted = build_harness_acceptance(self.root, self._run_state(), "final_gate_node")
        self.assertEqual(converted[0]["argv"], ["python", "-m", "pytest"])
        self.assertEqual(converted[0]["expected_exit_code"], 0)
        self.assertEqual(converted[1]["type"], "unsupported_shell")

    def test_non_retryable_task_result_blocks_graphflow_node_retry(self) -> None:
        """验证 Harness 不可重试分类会阻断 GraphFlow 节点级自动重放。"""
        state = self._run_state()
        policy = RetryPolicy(max_retries=3)
        self.assertFalse(policy.can_retry(
            state,
            "implementation-1",
            "implementation",
            "harness_non_retryable:cancelled: stopped",
        ))
        self.assertTrue(policy.can_retry(
            state,
            "implementation-1",
            "implementation",
            "harness_retryable:validation: failed",
        ))

    def _run_state(self) -> RunState:
        return RunState(
            "run-1",
            "RUNNING",
            SpecRef("spec-1", 1, "sha256:spec", "specs/spec-1.json"),
        )
