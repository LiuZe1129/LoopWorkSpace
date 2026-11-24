"""验证 Agent-Harness 阶段 7 的本地快照、回写、异常收口与生产装配。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.agent_loop import AgentLoop
from harness.contracts import Task, TaskBudget, TaskPermissions, TaskResultStatus, WorkspaceConfig
from harness.controller import HarnessController
from harness.integration import build_agent_profile
from harness.models import ModelGateway
from harness.runtime_factory import RuntimeConfig, RuntimeFactory
from harness.persistence.atomic import _exclusive_lock
from harness.sandbox import SandboxManager
from harness.task_runner import HarnessTaskRunner
from harness.tools import PolicyEngine, ToolRouter, build_mvp_registry
from harness.tools.ledger import ToolLedger
from harness.validation import DeterministicValidator
from harness.workspace import WorkspaceManager
from harness.errors import WorkspaceConflictError, WorkspacePreparationError
from tests.test_harness_stage4 import DecisionProvider, LoopSandboxRuntime


class HarnessStageSevenTests(unittest.TestCase):
    """覆盖 P0 七项缺口中的可自动验证路径，位于 Harness MVP 发布门禁。"""

    def setUp(self) -> None:
        """创建独立本地工程，位于 Harness 阶段 7 测试准备环节。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "source.txt").write_text("base\n", encoding="utf-8")
        self.controller = HarnessController(self.root)
        self.runtime = LoopSandboxRuntime()
        self.sandboxes = SandboxManager(self.root, self.runtime)

    def tearDown(self) -> None:
        """清理测试工程，位于 Harness 阶段 7 测试收尾环节。"""
        self.temporary.cleanup()

    def profile(self):
        """构造确定性 AgentProfile，位于 Harness 模型输入准备环节。"""
        return build_agent_profile(
            agent_id="stage7-agent",
            version="1.0.0",
            prompt="只执行单任务",
            model="fake-model",
            max_input_tokens=2000,
            max_output_tokens=200,
            tools=("read_file", "write_file"),
        )

    def runner(self, decisions: list[dict]) -> HarnessTaskRunner:
        """装配 Fake Provider 与 Sandbox，位于 Harness 阶段 7 端到端测试入口。"""
        router = ToolRouter(
            build_mvp_registry(),
            PolicyEngine(),
            ToolLedger(self.root),
            self.sandboxes,
            platform_tools=frozenset({"read_file", "write_file", "shell_exec"}),
            event_store=self.controller.event_store,
        )
        loop = AgentLoop(self.controller, ModelGateway(DecisionProvider(decisions), max_attempts=1), router)
        return HarnessTaskRunner(
            self.root,
            self.controller,
            loop,
            DeterministicValidator(router),
            self.sandboxes,
        )

    def task(self, profile, *, source_root: str = ".") -> Task:
        """构造本地快照 Task，位于 GraphFlow 节点进入 Harness 的契约边界。"""
        return Task(
            task_id="task-stage7",
            graph_run_id="run-stage7",
            node_id="implementation-stage7",
            objective="写入结果文件",
            agent_profile=profile.to_ref(),
            permissions=TaskPermissions(("read_file", "write_file")),
            budget=TaskBudget(max_rounds=3, max_tool_calls=3, max_model_tokens=500),
            workspace=WorkspaceConfig(source_root=source_root),
            acceptance=({"type": "file_contains", "path": "result.txt", "text": "done"},),
        )

    def test_validated_changes_are_written_back_with_changeset(self) -> None:
        """验证 Harness 只在验收通过后回写，并输出可审计 ChangeSet。"""
        profile = self.profile()
        result = self.runner([
            {
                "action": "tool_call",
                "tool_name": "write_file",
                "arguments": {"path": "result.txt", "content": "done"},
                "idempotency_key": "stage7-write",
            },
            {"action": "complete", "result": {"summary": "done"}},
        ]).run(self.task(profile), profile)
        self.assertEqual(TaskResultStatus.SUCCEEDED, result.status)
        self.assertEqual("done", (self.root / "result.txt").read_text(encoding="utf-8"))
        self.assertTrue(result.changeset["applied"])
        self.assertIn("result.txt", result.changeset["created"])

    def test_writeback_rejects_source_conflict(self) -> None:
        """验证本地源文件并发变化时 Harness 回写失败关闭且不覆盖用户内容。"""
        manager = WorkspaceManager(self.root)
        profile = self.profile()
        task = self.task(profile)
        workspace = self.sandboxes.workspace_path("exec-conflict", "attempt-conflict")
        session = manager.prepare(task, "exec-conflict", "attempt-conflict", workspace)
        assert session is not None
        (workspace / "source.txt").write_text("agent\n", encoding="utf-8")
        changeset = manager.create_changeset(session)
        (self.root / "source.txt").write_text("user\n", encoding="utf-8")
        with self.assertRaises(WorkspaceConflictError):
            manager.apply(session, changeset)
        self.assertEqual("user\n", (self.root / "source.txt").read_text(encoding="utf-8"))

    def test_prepare_failure_closes_execution_state(self) -> None:
        """验证快照准备异常经过 Harness Guard 后不会遗留运行中 Execution。"""
        profile = self.profile()
        with self.assertRaises(WorkspacePreparationError):
            self.runner([]).run(self.task(profile, source_root="../outside"), profile)
        execution = self.controller.task_store.list_executions()[0]
        attempt = self.controller.task_store.load_attempt(execution.execution_id, execution.active_attempt_id or "")
        self.assertEqual("FAILED", execution.status.value)
        self.assertEqual("FAILED", attempt.status.value)

    def test_runtime_factory_builds_real_graphflow_handler(self) -> None:
        """验证生产 RuntimeFactory 将 GraphFlow Handler 接入同一套 Harness 基础设施。"""
        factory = RuntimeFactory(
            self.root,
            config=RuntimeConfig(docker_image="loop-harness:test"),
            provider=DecisionProvider([]),
            sandbox_runtime=self.runtime,
        )
        assembled = factory.build()
        self.assertIsNotNone(assembled.handler)
        self.assertIs(assembled.controller, assembled.runner._controller)

    def test_stale_lock_file_does_not_block_os_lock(self) -> None:
        """验证遗留锁文件不会阻断 Harness，实际互斥由进程级 OS 文件锁决定。"""
        target = self.root / "record.json"
        target.with_name(".record.json.lock").write_text("stale-owner", encoding="utf-8")
        with _exclusive_lock(target, timeout_seconds=0.2):
            target.write_text("locked", encoding="utf-8")
        self.assertEqual("locked", target.read_text(encoding="utf-8"))

    def test_production_can_require_immutable_image_digest(self) -> None:
        """验证 Harness 发布配置可强制 Docker 镜像使用不可变 sha256 摘要。"""
        factory = RuntimeFactory(
            self.root,
            config=RuntimeConfig(docker_image="loop-harness:latest", require_image_digest=True),
            provider=DecisionProvider([]),
            sandbox_runtime=self.runtime,
        )
        with self.assertRaisesRegex(RuntimeError, "sha256"):
            factory.build()


if __name__ == "__main__":
    unittest.main()
