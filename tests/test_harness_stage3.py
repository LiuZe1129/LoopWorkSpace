"""验证 Agent-Harness 阶段 3 的 Tool、Policy、Ledger 与单 Sandbox 基础设施。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.contracts import Task, TaskBudget, TaskPermissions
from harness.integration import build_agent_profile
from harness.persistence import EventStore
from harness.sandbox import (
    DockerSandboxRuntime,
    SandboxExecRequest,
    SandboxExecResult,
    SandboxHandle,
    SandboxInfo,
    SandboxManager,
    SandboxSpec,
)
from harness.sandbox.docker_runtime import CommandOutcome
from harness.tools import PolicyEngine, ToolStatus, ToolRouter, build_mvp_registry
from harness.tools.contracts import ToolCall
from harness.tools.ledger import LedgerStatus, ToolLedger


class FakeSandboxRuntime:
    """模拟容器文件系统，位于 Harness 单元测试显式注入的 Sandbox 边界。"""

    def __init__(self) -> None:
        self.exec_count = 0
        self.cancel_count = 0
        self.destroy_count = 0
        self.handles: dict[str, SandboxHandle] = {}
        self.fail_next = False

    def create(self, spec: SandboxSpec) -> SandboxHandle:
        """创建测试 Handle，模拟 Harness Attempt 绑定 Sandbox。"""
        handle = SandboxHandle(spec.sandbox_id, spec.attempt_id, f"fake-{spec.sandbox_id}", spec.workspace)
        self.handles[handle.sandbox_id] = handle
        return handle

    def exec(self, handle: SandboxHandle, request: SandboxExecRequest) -> SandboxExecResult:
        """模拟容器内执行，供 Harness Tool Router 测试且绝不启动宿主 Shell。"""
        if self.fail_next:
            self.fail_next = False
            raise OSError("temporary sandbox transport failure")
        self.exec_count += 1
        if request.argv[0] == "python":
            relative = request.argv[-1]
            target = handle.workspace / relative
            if "write_bytes" in request.argv[2]:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(request.stdin or b"")
                return SandboxExecResult(0, b"", b"", duration_ms=2)
            if not target.is_file():
                return SandboxExecResult(1, b"", b"not found", duration_ms=1)
            return SandboxExecResult(0, target.read_bytes(), b"", duration_ms=1)
        if request.argv[0] == "simulate-timeout":
            self.cancel(handle)
            return SandboxExecResult(None, b"", b"", timed_out=True, duration_ms=100)
        if request.argv[0] == "simulate-output-limit":
            self.cancel(handle)
            return SandboxExecResult(-1, b"x" * 10, b"", output_limit_exceeded=True, duration_ms=2)
        if request.argv[0] == "echo":
            return SandboxExecResult(0, " ".join(request.argv[1:]).encode(), b"", duration_ms=1)
        return SandboxExecResult(7, b"", b"command failed", duration_ms=1)

    def cancel(self, handle: SandboxHandle) -> None:
        """记录取消，模拟 Harness 超时传播到整个 Attempt Sandbox。"""
        self.cancel_count += 1

    def destroy(self, handle: SandboxHandle) -> None:
        """记录销毁，模拟 Harness Artifact 收集后的资源回收。"""
        self.destroy_count += 1
        self.handles.pop(handle.sandbox_id, None)

    def inspect(self, handle: SandboxHandle) -> SandboxInfo:
        """返回运行状态，模拟 Harness 恢复流程的 Sandbox 检查。"""
        return SandboxInfo(handle.sandbox_id, handle.runtime_id, "running")


class RecordingCommandRunner:
    """记录 Docker argv，位于 Harness Docker Runtime 隔离参数的测试边界。"""

    def __init__(self, outcomes: list[CommandOutcome]):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def run(self, argv, *, timeout_seconds, max_output_bytes, stdin=None) -> CommandOutcome:
        """返回预设 CLI 结果，供 Harness 验证 Docker 命令而不依赖本机安装。"""
        self.calls.append(
            {
                "argv": list(argv),
                "timeout_seconds": timeout_seconds,
                "max_output_bytes": max_output_bytes,
                "stdin": stdin,
            }
        )
        return self.outcomes.pop(0)


class HarnessStageThreeTests(unittest.TestCase):
    """覆盖阶段 3，确认 Harness Tool 只能经 Policy、Ledger 和 Sandbox 执行。"""

    def setUp(self) -> None:
        """创建隔离目录和 Fake Sandbox，位于 Harness Tool 测试准备环节。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime = FakeSandboxRuntime()
        self.manager = SandboxManager(self.root, self.runtime)
        self.handle = self.manager.create("exec-1", "attempt-1")
        self.registry = build_mvp_registry()
        self.ledger = ToolLedger(self.root)
        self.events = EventStore(self.root)

    def tearDown(self) -> None:
        """回收隔离目录，位于 Harness Tool 测试收尾环节。"""
        self.temporary.cleanup()

    def profile(self, tools=("read_file", "write_file", "shell_exec")):
        """构造声明工具的 Profile，位于 Harness Policy 权限交集测试输入环节。"""
        return build_agent_profile(
            agent_id="agent-1",
            version="1",
            prompt="测试 Agent",
            model="test-model",
            max_input_tokens=1000,
            max_output_tokens=100,
            tools=tuple(tools),
        )

    def task(self, tools=("read_file", "write_file", "shell_exec")) -> Task:
        """构造授权工具的 Task，位于 GraphFlow 到 Harness Policy 测试边界。"""
        profile = self.profile()
        return Task(
            task_id="task-1",
            graph_run_id="run-1",
            node_id="node-1",
            objective="执行受控工具",
            agent_profile=profile.to_ref(),
            permissions=TaskPermissions(tuple(tools)),
            budget=TaskBudget(),
        )

    def router(self, **kwargs) -> ToolRouter:
        """构造唯一 Tool Router，位于 Harness Tool 管线集成测试入口。"""
        return ToolRouter(
            self.registry,
            PolicyEngine(),
            self.ledger,
            self.manager,
            platform_tools=frozenset({"read_file", "write_file", "shell_exec"}),
            event_store=self.events,
            **kwargs,
        )

    def route(self, router: ToolRouter, tool_name: str, arguments: dict, key: str, *, task_tools=None, profile_tools=None):
        """提交标准 ToolCall，位于 Harness Router 测试的便捷调用环节。"""
        profile = self.profile(profile_tools or ("read_file", "write_file", "shell_exec"))
        task = Task(
            task_id="task-1",
            graph_run_id="run-1",
            node_id="node-1",
            objective="执行受控工具",
            agent_profile=profile.to_ref(),
            permissions=TaskPermissions(tuple(task_tools or ("read_file", "write_file", "shell_exec"))),
            budget=TaskBudget(),
        )
        return router.route(
            task,
            profile,
            execution_id="exec-1",
            attempt_id="attempt-1",
            sandbox_id=self.handle.sandbox_id,
            tool_name=tool_name,
            arguments=arguments,
            idempotency_key=key,
        )

    def test_unknown_and_invalid_tools_are_denied_before_execution(self) -> None:
        """验证 Registry 与 Schema 门禁，确保 Harness 不执行未知或非法 ToolCall。"""
        router = self.router()
        unknown = self.route(router, "delete_everything", {}, "key-1")
        invalid = self.route(router, "read_file", {}, "key-2")
        self.assertEqual(unknown.error_code, "unknown_tool")
        self.assertEqual(invalid.error_code, "invalid_arguments")
        self.assertTrue(unknown.call_id.startswith("call-"))
        self.assertEqual(self.runtime.exec_count, 0)

    def test_task_permission_overrides_agent_declaration(self) -> None:
        """验证权限交集，确保 Agent 声明工具但 Task 未授权时 Harness 仍拒绝。"""
        result = self.route(
            self.router(),
            "shell_exec",
            {"argv": ["echo", "blocked"]},
            "key-1",
            task_tools=("read_file",),
            profile_tools=("shell_exec",),
        )
        self.assertEqual(result.status, ToolStatus.DENIED)
        self.assertEqual(result.error_code, "task_not_authorized")
        self.assertEqual(self.runtime.exec_count, 0)

    def test_task_profile_mismatch_is_denied(self) -> None:
        """验证 Profile 绑定，确保 Harness 调用方不能替换为声明更多工具的 Agent。"""
        task_profile = self.profile(("read_file",))
        elevated_profile = build_agent_profile(
            agent_id="agent-1",
            version="elevated",
            prompt="高权限 Agent",
            model="test-model",
            max_input_tokens=1000,
            max_output_tokens=100,
            tools=("shell_exec",),
        )
        task = Task(
            "task-1",
            "run-1",
            "node-1",
            "执行命令",
            task_profile.to_ref(),
            TaskPermissions(("shell_exec",)),
            TaskBudget(),
        )
        result = self.router().route(
            task,
            elevated_profile,
            execution_id="exec-1",
            attempt_id="attempt-1",
            sandbox_id=self.handle.sandbox_id,
            tool_name="shell_exec",
            arguments={"argv": ["echo", "never"]},
            idempotency_key="profile-mismatch",
        )
        self.assertEqual(result.error_code, "agent_profile_mismatch")
        self.assertEqual(self.runtime.exec_count, 0)

    def test_sandbox_cannot_be_reused_by_another_attempt(self) -> None:
        """验证 Sandbox 归属，确保 Harness 不允许跨 Attempt 共享容器和工作区。"""
        profile = self.profile(("read_file",))
        task = Task(
            "task-1",
            "run-1",
            "node-1",
            "读取文件",
            profile.to_ref(),
            TaskPermissions(("read_file",)),
            TaskBudget(),
        )
        result = self.router().route(
            task,
            profile,
            execution_id="exec-1",
            attempt_id="attempt-2",
            sandbox_id=self.handle.sandbox_id,
            tool_name="read_file",
            arguments={"path": "a.txt"},
            idempotency_key="wrong-attempt",
        )
        self.assertEqual(result.error_code, "sandbox_unavailable")

    def test_workspace_traversal_is_denied_before_ledger(self) -> None:
        """验证路径越界，确保 Harness 不把绝对路径或父目录交给 Sandbox。"""
        result = self.route(self.router(), "read_file", {"path": "../secret"}, "key-traversal")
        self.assertEqual(result.error_code, "invalid_arguments")
        self.assertEqual(self.runtime.exec_count, 0)

    def test_write_then_read_reuses_one_sandbox(self) -> None:
        """验证三个层次闭环，确保 Harness Attempt 内文件工具复用同一 Workspace。"""
        router = self.router()
        written = self.route(router, "write_file", {"path": "src/demo.txt", "content": "你好"}, "write-1")
        read = self.route(router, "read_file", {"path": "src/demo.txt"}, "read-1")
        self.assertEqual(written.status, ToolStatus.SUCCEEDED)
        self.assertEqual(read.output["content"], "你好")
        self.assertEqual(written.output["sha256"], read.output["sha256"])
        self.assertEqual(self.runtime.exec_count, 2)

    def test_completed_idempotency_key_does_not_repeat_side_effect(self) -> None:
        """验证幂等重放，确保 Harness 重复 write_file 不产生第二次副作用。"""
        router = self.router()
        first = self.route(router, "write_file", {"path": "a.txt", "content": "one"}, "same-key")
        second = self.route(router, "write_file", {"path": "a.txt", "content": "one"}, "same-key")
        self.assertEqual(first, second)
        self.assertEqual(self.runtime.exec_count, 1)
        self.assertTrue(self.events.read("exec-1")[-1].payload["replayed"])

    def test_started_ledger_becomes_ambiguous_instead_of_replay(self) -> None:
        """验证崩溃恢复，确保 Harness 未完成副作用不会被自动重放。"""
        call = ToolCall(
            "old-call",
            "exec-1",
            "attempt-1",
            "write_file",
            {"path": "a.txt", "content": "one"},
            "crashed-key",
        )
        self.assertEqual(self.ledger.begin(call).status, LedgerStatus.STARTED)
        result = self.route(
            self.router(),
            "write_file",
            {"path": "a.txt", "content": "one"},
            "crashed-key",
        )
        self.assertEqual(result.status, ToolStatus.AMBIGUOUS)
        self.assertEqual(self.runtime.exec_count, 0)

    def test_started_write_with_matching_hash_is_recovered_without_replay(self) -> None:
        """验证 write_file hash 恢复，确保 Harness 已落盘副作用不会被重复执行。"""
        call = ToolCall(
            "old-call",
            "exec-1",
            "attempt-1",
            "write_file",
            {"path": "recovered.txt", "content": "already-written"},
            "recovered-write",
        )
        self.ledger.begin(call)
        (self.handle.workspace / "recovered.txt").write_text("already-written", encoding="utf-8")
        result = self.route(
            self.router(),
            "write_file",
            {"path": "recovered.txt", "content": "already-written"},
            "recovered-write",
        )
        self.assertEqual(result.status, ToolStatus.SUCCEEDED)
        self.assertTrue(result.metadata["recovered_from_workspace_hash"])
        self.assertEqual(self.runtime.exec_count, 0)

    def test_read_only_infrastructure_failure_can_retry_safely(self) -> None:
        """验证只读恢复，确保 Harness read_file 失败后撤销 started 并允许安全重试。"""
        target = self.handle.workspace / "a.txt"
        target.write_text("safe", encoding="utf-8")
        self.runtime.fail_next = True
        router = self.router()
        failed = self.route(router, "read_file", {"path": "a.txt"}, "read-retry")
        retried = self.route(router, "read_file", {"path": "a.txt"}, "read-retry")
        self.assertEqual(failed.status, ToolStatus.FAILED)
        self.assertEqual(retried.status, ToolStatus.SUCCEEDED)
        self.assertEqual(retried.output["content"], "safe")

    def test_high_risk_tool_can_require_approval(self) -> None:
        """验证审批策略，确保 Harness 高风险 shell_exec 未批准时不进入 Sandbox。"""
        router = self.router(approval_required_tools=frozenset({"shell_exec"}))
        result = self.route(router, "shell_exec", {"argv": ["echo", "blocked"]}, "shell-1")
        self.assertEqual(result.status, ToolStatus.DENIED)
        self.assertEqual(result.error_code, "approval_required")

    def test_control_plane_approval_allows_high_risk_tool(self) -> None:
        """验证可信审批输入，确保 Harness 仅由 Router 配置批准高风险工具。"""
        router = self.router(
            approval_required_tools=frozenset({"shell_exec"}),
            approved_tools=frozenset({"shell_exec"}),
        )
        result = self.route(router, "shell_exec", {"argv": ["echo", "approved"]}, "shell-approved")
        self.assertEqual(result.status, ToolStatus.SUCCEEDED)
        self.assertEqual(result.output["stdout"], "approved")

    def test_shell_timeout_terminates_attempt_sandbox(self) -> None:
        """验证超时传播，确保 Harness Sandbox Runtime 终止目标容器而非仅返回错误。"""
        result = self.route(
            self.router(),
            "shell_exec",
            {"argv": ["simulate-timeout"]},
            "shell-timeout",
        )
        self.assertEqual(result.status, ToolStatus.TIMED_OUT)
        self.assertEqual(self.runtime.cancel_count, 1)

    def test_output_limit_terminates_attempt_sandbox(self) -> None:
        """验证输出上限，确保 Harness 不会无限缓存 ToolResult 且会终止失控容器。"""
        result = self.route(
            self.router(),
            "shell_exec",
            {"argv": ["simulate-output-limit"]},
            "shell-output-limit",
        )
        self.assertEqual(result.error_code, "output_limit_exceeded")
        self.assertEqual(self.runtime.cancel_count, 1)

    def test_missing_sandbox_never_falls_back_to_host(self) -> None:
        """验证基础设施失败关闭，确保 Harness 缺少 Sandbox 时不执行宿主命令。"""
        profile = self.profile(("shell_exec",))
        task = Task(
            "task-1",
            "run-1",
            "node-1",
            "执行命令",
            profile.to_ref(),
            TaskPermissions(("shell_exec",)),
            TaskBudget(),
        )
        result = self.router().route(
            task,
            profile,
            execution_id="exec-1",
            attempt_id="attempt-1",
            sandbox_id="missing",
            tool_name="shell_exec",
            arguments={"argv": ["echo", "never"]},
            idempotency_key="missing-sandbox",
        )
        self.assertEqual(result.error_code, "sandbox_unavailable")
        self.assertEqual(self.runtime.exec_count, 0)

    def test_destroy_collects_artifacts_before_runtime_cleanup(self) -> None:
        """验证销毁顺序，确保 Harness 先收集 Artifact 哈希再回收 Sandbox。"""
        target = self.handle.workspace / "result.txt"
        target.write_text("artifact", encoding="utf-8")
        manifest = self.manager.destroy(self.handle.sandbox_id)
        self.assertEqual(manifest.files[0]["path"], "result.txt")
        self.assertEqual(self.runtime.destroy_count, 1)
        stored = self.root / ".loop" / "harness" / "executions" / "exec-1" / "attempts" / "attempt-1" / "sandbox-artifacts.json"
        self.assertTrue(stored.is_file())

    def test_docker_runtime_builds_hardened_command_and_kills_on_timeout(self) -> None:
        """验证 Docker 参数和超时终止，确保 Harness 正式 Runtime 默认采用隔离配置。"""
        runner = RecordingCommandRunner(
            [
                CommandOutcome(0, b"container-1\n", b"", False, False, 1),
                CommandOutcome(-1, b"", b"", True, False, 100),
                CommandOutcome(0, b"container-1\n", b"", False, False, 1),
            ]
        )
        runtime = DockerSandboxRuntime(self.root, runner=runner)
        workspace = self.root / "docker-workspace"
        handle = runtime.create(SandboxSpec("sandbox-1", "attempt-1", workspace))
        create_argv = runner.calls[0]["argv"]
        self.assertIn("none", create_argv)
        self.assertIn("--read-only", create_argv)
        self.assertIn("none", create_argv[create_argv.index("--log-driver") + 1])
        self.assertIn("ALL", create_argv)
        self.assertIn("no-new-privileges=true", create_argv)
        self.assertIn("--pids-limit", create_argv)
        self.assertIn("--memory", create_argv)
        self.assertIn("--cpus", create_argv)
        result = runtime.exec(handle, SandboxExecRequest(("sleep", "99"), 1, 1024))
        self.assertTrue(result.timed_out)
        self.assertEqual(runner.calls[-1]["argv"][:2], ["docker", "kill"])


if __name__ == "__main__":
    unittest.main()
