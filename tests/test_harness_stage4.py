"""验证 Agent-Harness 阶段 4 的多轮循环、预算、取消、Checkpoint 与恢复。"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from harness.agent_loop import AgentLoop, AgentLoopStatus
from harness.budget import BudgetMeter
from harness.cancellation import CancellationToken
from harness.context_manager import ContextManager
from harness.contracts import AttemptStatus, Task, TaskBudget, TaskExecutionStatus, TaskPermissions
from harness.controller import HarnessController
from harness.integration import build_agent_profile
from harness.models import ModelError, ModelGateway, ModelRequest, ModelResponse, ModelUsage
from harness.recovery import Reconciler, RecoveryStatus
from harness.sandbox import (
    SandboxExecRequest,
    SandboxExecResult,
    SandboxHandle,
    SandboxInfo,
    SandboxManager,
    SandboxSpec,
)
from harness.tools import PolicyEngine, ToolRouter, build_mvp_registry
from harness.tools.contracts import ToolCall
from harness.tools.ledger import ToolLedger


class LoopSandboxRuntime:
    """模拟持久单容器，位于 Harness Agent Loop 集成测试的显式 Fake Sandbox 边界。"""

    def __init__(self) -> None:
        self.handles: dict[str, SandboxHandle] = {}
        self.statuses: dict[str, str] = {}
        self.cancel_count = 0
        self.destroy_count = 0
        self.block_commands = False
        self.command_started = threading.Event()
        self.command_released = threading.Event()

    def create(self, spec: SandboxSpec) -> SandboxHandle:
        """创建 Fake Handle，模拟 Harness Attempt 的 Sandbox 准备环节。"""
        handle = SandboxHandle(spec.sandbox_id, spec.attempt_id, f"runtime-{spec.sandbox_id}", spec.workspace)
        self.handles[handle.runtime_id] = handle
        self.statuses[handle.runtime_id] = "running"
        return handle

    def exec(self, handle: SandboxHandle, request: SandboxExecRequest) -> SandboxExecResult:
        """模拟文件与命令执行，供 Harness 多轮 Tool Observation 测试。"""
        if self.block_commands and request.argv[0] == "wait-for-cancel":
            self.command_started.set()
            self.command_released.wait(timeout=5)
            return SandboxExecResult(None, b"", b"cancelled", timed_out=True, duration_ms=100)
        if request.argv[0] == "python":
            relative = request.argv[-1]
            target = handle.workspace / relative
            if "write_bytes" in request.argv[2]:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(request.stdin or b"")
                return SandboxExecResult(0, b"", b"", duration_ms=1)
            if target.is_file():
                return SandboxExecResult(0, target.read_bytes(), b"", duration_ms=1)
            return SandboxExecResult(1, b"", b"not found", duration_ms=1)
        return SandboxExecResult(0, b"ok", b"", duration_ms=1)

    def cancel(self, handle: SandboxHandle) -> None:
        """释放阻塞命令并标记退出，模拟 Harness 取消传播到整个 Sandbox。"""
        self.cancel_count += 1
        self.statuses[handle.runtime_id] = "exited"
        self.command_released.set()

    def destroy(self, handle: SandboxHandle) -> None:
        """记录资源回收，模拟 Harness 终态销毁 Sandbox。"""
        self.destroy_count += 1
        self.statuses[handle.runtime_id] = "destroyed"

    def inspect(self, handle: SandboxHandle) -> SandboxInfo:
        """返回 Fake 容器状态，供 Harness Reconciler 重启恢复测试。"""
        return SandboxInfo(handle.sandbox_id, handle.runtime_id, self.statuses.get(handle.runtime_id, "missing"))


class DecisionProvider:
    """按序返回 Agent 决策，位于 Harness 多轮 Model Gateway 的确定性测试边界。"""

    def __init__(self, decisions: list[dict | Exception], *, tokens_per_call: int = 10):
        self.decisions = list(decisions)
        self.tokens_per_call = tokens_per_call
        self.calls = 0
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest, *, timeout_seconds: float) -> ModelResponse:
        """返回一个结构化决策，模拟 Harness Agent 每轮推理。"""
        self.calls += 1
        self.requests.append(request)
        selected = self.decisions.pop(0)
        if isinstance(selected, Exception):
            raise selected
        return ModelResponse(
            response_id=f"response-{self.calls}",
            model=request.model,
            text=json.dumps(selected, ensure_ascii=False),
            finish_reason="completed",
            usage=ModelUsage(
                input_tokens=self.tokens_per_call // 2,
                output_tokens=self.tokens_per_call - self.tokens_per_call // 2,
                total_tokens=self.tokens_per_call,
                cost=0.01,
            ),
        )


class BlockingModelProvider:
    """模拟可取消模型请求，位于 Harness Gateway 活动取消传播的测试边界。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.released = threading.Event()
        self.cancelled_request_id: str | None = None

    def complete(self, request: ModelRequest, *, timeout_seconds: float) -> ModelResponse:
        """阻塞到 cancel，模拟 Harness 正在进行的远程模型请求。"""
        self.started.set()
        self.released.wait(timeout=5)
        return ModelResponse("cancelled-response", request.model, "{}", "cancelled")

    def cancel(self, request_id: str) -> None:
        """释放阻塞请求，模拟 Harness Provider 主动关闭网络响应。"""
        self.cancelled_request_id = request_id
        self.released.set()


class HarnessStageFourTests(unittest.TestCase):
    """覆盖阶段 4，确认 Harness 单 Agent 多轮执行可停止、可取消且可恢复。"""

    def setUp(self) -> None:
        """创建完整 Harness 运行组件，位于阶段 4 集成测试准备环节。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.controller = HarnessController(self.root)
        self.runtime = LoopSandboxRuntime()
        self.sandboxes = SandboxManager(self.root, self.runtime)
        self.ledger = ToolLedger(self.root)

    def tearDown(self) -> None:
        """回收隔离运行目录，位于 Harness 阶段 4 测试收尾环节。"""
        self.temporary.cleanup()

    def make_profile(self):
        """构造 Loop Profile，位于 Harness Model 与 Tool 权限测试输入环节。"""
        return build_agent_profile(
            agent_id="loop-agent",
            version="1.0.0",
            prompt="你是单 Agent Harness，只输出结构化决策。",
            model="test-model",
            max_input_tokens=4000,
            max_output_tokens=200,
            tools=("read_file", "write_file", "shell_exec"),
        )

    def make_task(self, profile, *, budget: TaskBudget | None = None) -> Task:
        """构造 Loop Task，位于 GraphFlow 到 Harness 多轮执行测试边界。"""
        return Task(
            task_id="task-loop",
            graph_run_id="run-1",
            node_id="node-1",
            objective="写入文件、读取验证并返回完成摘要",
            agent_profile=profile.to_ref(),
            permissions=TaskPermissions(("read_file", "write_file", "shell_exec")),
            budget=budget or TaskBudget(max_rounds=10, max_tool_calls=10, max_model_tokens=10_000),
        )

    def prepare(self, task: Task):
        """创建并启动 Execution，位于 Harness Loop 测试的生命周期准备环节。"""
        self.controller.register_task(task)
        self.controller.create_execution(task.task_id, execution_id="exec-loop", attempt_id="attempt-1")
        lease = self.controller.lease_manager.acquire("exec-loop", "worker-1", ttl_seconds=300)
        execution, attempt = self.controller.start_execution("exec-loop", lease, self.sandboxes)
        return lease, execution, attempt

    def make_loop(self, provider: DecisionProvider) -> AgentLoop:
        """装配 Model/Tool/Controller，位于 Harness 多轮执行测试入口。"""
        router = ToolRouter(
            build_mvp_registry(),
            PolicyEngine(),
            self.ledger,
            self.sandboxes,
            platform_tools=frozenset({"read_file", "write_file", "shell_exec"}),
            event_store=self.controller.event_store,
        )
        return AgentLoop(
            self.controller,
            ModelGateway(provider, max_attempts=1, sleeper=lambda _: None),
            router,
            context_manager=ContextManager(),
            budget_meter=BudgetMeter(),
        )

    def test_agent_completes_two_tool_calls_and_finalizes(self) -> None:
        """验证多轮主路径，确保 Harness 至少执行两次 ToolCall 后提出完成。"""
        profile = self.make_profile()
        task = self.make_task(profile)
        lease, _, attempt = self.prepare(task)
        provider = DecisionProvider(
            [
                {
                    "action": "tool_call",
                    "tool_name": "write_file",
                    "arguments": {"path": "result.txt", "content": "done"},
                    "idempotency_key": "write-result",
                },
                {
                    "action": "tool_call",
                    "tool_name": "read_file",
                    "arguments": {"path": "result.txt"},
                    "idempotency_key": "read-result",
                },
                {"action": "complete", "result": {"summary": "verified"}},
            ]
        )
        outcome = self.make_loop(provider).run(
            task,
            profile,
            execution_id="exec-loop",
            attempt_id="attempt-1",
            sandbox_id=attempt.sandbox_ref,
            lease=lease,
        )
        self.assertEqual(outcome.status, AgentLoopStatus.COMPLETED)
        persisted = self.controller.task_store.load_attempt("exec-loop", "attempt-1")
        self.assertEqual((persisted.rounds_used, persisted.tool_calls_used), (3, 2))
        self.assertEqual(persisted.model_tokens_used, 30)
        checkpoint = self.controller.load_last_checkpoint("exec-loop")
        self.assertEqual(checkpoint.stage, "completion_proposed")
        execution, finalized, manifest = self.controller.finalize_execution(
            "exec-loop",
            "attempt-1",
            lease,
            self.sandboxes,
            outcome=outcome.status.value,
        )
        self.assertEqual(execution.status, TaskExecutionStatus.VALIDATING_RESULT)
        self.assertEqual(finalized.status, AttemptStatus.SUCCEEDED)
        self.assertEqual(manifest.files[0]["path"], "result.txt")

    def test_round_budget_stops_loop_without_infinite_model_calls(self) -> None:
        """验证轮次预算，确保 Harness 达到上限后不再请求模型。"""
        profile = self.make_profile()
        task = self.make_task(
            profile,
            budget=TaskBudget(max_rounds=1, max_tool_calls=10, max_model_tokens=10_000),
        )
        lease, _, attempt = self.prepare(task)
        provider = DecisionProvider(
            [
                {"action": "tool_call", "tool_name": "read_file", "arguments": {"path": "missing"}, "idempotency_key": "read-1"},
                {"action": "complete", "result": {"summary": "must-not-run"}},
            ]
        )
        outcome = self.make_loop(provider).run(
            task,
            profile,
            execution_id="exec-loop",
            attempt_id="attempt-1",
            sandbox_id=attempt.sandbox_ref,
            lease=lease,
        )
        self.assertEqual(outcome.status, AgentLoopStatus.BUDGET_EXHAUSTED)
        self.assertEqual(outcome.error_code, "rounds")
        self.assertEqual(provider.calls, 1)

    def test_token_usage_is_committed_before_budget_stop(self) -> None:
        """验证模型超额后记账，确保 Harness 不因停止执行而丢失已产生 Token。"""
        profile = self.make_profile()
        task = self.make_task(
            profile,
            budget=TaskBudget(max_rounds=5, max_tool_calls=5, max_model_tokens=1000),
        )
        lease, _, attempt = self.prepare(task)
        provider = DecisionProvider(
            [{"action": "complete", "result": {"summary": "large"}}],
            tokens_per_call=1200,
        )
        outcome = self.make_loop(provider).run(
            task,
            profile,
            execution_id="exec-loop",
            attempt_id="attempt-1",
            sandbox_id=attempt.sandbox_ref,
            lease=lease,
        )
        self.assertEqual(outcome.status, AgentLoopStatus.BUDGET_EXHAUSTED)
        persisted = self.controller.task_store.load_attempt("exec-loop", "attempt-1")
        self.assertEqual(persisted.model_tokens_used, 1200)

    def test_model_failure_is_finite(self) -> None:
        """验证模型错误收敛，确保 Harness Gateway 失败不会形成无限 Agent Loop。"""
        profile = self.make_profile()
        task = self.make_task(profile)
        lease, _, attempt = self.prepare(task)
        provider = DecisionProvider([ModelError("provider_down", "unavailable", retryable=False)])
        outcome = self.make_loop(provider).run(
            task,
            profile,
            execution_id="exec-loop",
            attempt_id="attempt-1",
            sandbox_id=attempt.sandbox_ref,
            lease=lease,
        )
        self.assertEqual(outcome.status, AgentLoopStatus.FAILED)
        self.assertEqual(provider.calls, 1)
        persisted = self.controller.task_store.load_attempt("exec-loop", "attempt-1")
        self.assertEqual(persisted.rounds_used, 1)

    def test_cancel_request_propagates_to_running_sandbox(self) -> None:
        """验证活动取消，确保 Harness 用户请求能终止正在执行的 Sandbox 命令。"""
        profile = self.make_profile()
        task = self.make_task(profile)
        lease, _, attempt = self.prepare(task)
        self.runtime.block_commands = True
        provider = DecisionProvider(
            [
                {
                    "action": "tool_call",
                    "tool_name": "shell_exec",
                    "arguments": {"argv": ["wait-for-cancel"]},
                    "idempotency_key": "blocking-shell",
                }
            ]
        )
        outcome_box = []
        token = CancellationToken()

        def run_loop() -> None:
            outcome_box.append(
                self.make_loop(provider).run(
                    task,
                    profile,
                    execution_id="exec-loop",
                    attempt_id="attempt-1",
                    sandbox_id=attempt.sandbox_ref,
                    lease=lease,
                    cancellation=token,
                )
            )

        worker = threading.Thread(target=run_loop)
        worker.start()
        self.assertTrue(self.runtime.command_started.wait(timeout=3))
        self.controller.request_cancel(
            "exec-loop",
            reason="test_cancel",
            sandboxes=self.sandboxes,
            cancellation=token,
        )
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.runtime.cancel_count, 1)
        self.assertEqual(outcome_box[0].status, AgentLoopStatus.CANCELLED)

    def test_cancel_request_propagates_to_active_model_provider(self) -> None:
        """验证模型取消，确保 Harness Gateway 通知支持取消的活动 Provider。"""
        profile = self.make_profile()
        task = self.make_task(profile)
        lease, _, attempt = self.prepare(task)
        provider = BlockingModelProvider()
        token = CancellationToken()
        router = ToolRouter(
            build_mvp_registry(),
            PolicyEngine(),
            self.ledger,
            self.sandboxes,
            platform_tools=frozenset({"read_file", "write_file", "shell_exec"}),
        )
        loop = AgentLoop(
            self.controller,
            ModelGateway(provider, max_attempts=1, sleeper=lambda _: None),
            router,
        )
        outcomes = []
        worker = threading.Thread(
            target=lambda: outcomes.append(
                loop.run(
                    task,
                    profile,
                    execution_id="exec-loop",
                    attempt_id="attempt-1",
                    sandbox_id=attempt.sandbox_ref,
                    lease=lease,
                    cancellation=token,
                )
            )
        )
        worker.start()
        self.assertTrue(provider.started.wait(timeout=3))
        self.controller.request_cancel("exec-loop", reason="cancel_model", cancellation=token)
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertIsNotNone(provider.cancelled_request_id)
        self.assertEqual(outcomes[0].status, AgentLoopStatus.CANCELLED)

    def test_pause_preserves_sandbox_and_resume_continues(self) -> None:
        """验证暂停恢复，确保 Harness 不销毁 Sandbox 且 Attempt 预算保持连续。"""
        profile = self.make_profile()
        task = self.make_task(profile)
        lease, _, attempt = self.prepare(task)
        self.controller.request_pause("exec-loop", reason="operator_pause")
        paused_provider = DecisionProvider([{"action": "complete", "result": {"summary": "unused"}}])
        paused = self.make_loop(paused_provider).run(
            task,
            profile,
            execution_id="exec-loop",
            attempt_id="attempt-1",
            sandbox_id=attempt.sandbox_ref,
            lease=lease,
        )
        self.assertEqual(paused.status, AgentLoopStatus.PAUSED)
        self.assertEqual(paused_provider.calls, 0)
        state = self.controller.pause_execution("exec-loop", lease)
        self.assertEqual(state.status, TaskExecutionStatus.PAUSED)
        self.assertEqual(self.runtime.destroy_count, 0)
        self.assertEqual(self.sandboxes.inspect(attempt.sandbox_ref).status, "running")

        self.controller.resume_execution("exec-loop", lease)
        resumed_provider = DecisionProvider([{"action": "complete", "result": {"summary": "resumed"}}])
        resumed = self.make_loop(resumed_provider).run(
            task,
            profile,
            execution_id="exec-loop",
            attempt_id="attempt-1",
            sandbox_id=attempt.sandbox_ref,
            lease=lease,
        )
        self.assertEqual(resumed.status, AgentLoopStatus.COMPLETED)
        persisted = self.controller.task_store.load_attempt("exec-loop", "attempt-1")
        self.assertEqual(persisted.rounds_used, 1)

    def test_resume_from_tool_checkpoint_preserves_usage(self) -> None:
        """验证服务重启恢复，确保 Harness 从最后 Checkpoint 继续且预算不清零。"""
        profile = self.make_profile()
        task = self.make_task(profile)
        lease, _, attempt = self.prepare(task)
        first_provider = DecisionProvider(
            [
                {
                    "action": "tool_call",
                    "tool_name": "write_file",
                    "arguments": {"path": "resume.txt", "content": "kept"},
                    "idempotency_key": "write-before-restart",
                },
                ModelError("process_crash", "simulated restart", retryable=False),
            ]
        )
        first = self.make_loop(first_provider).run(
            task,
            profile,
            execution_id="exec-loop",
            attempt_id="attempt-1",
            sandbox_id=attempt.sandbox_ref,
            lease=lease,
        )
        self.assertEqual(first.status, AgentLoopStatus.FAILED)
        before = self.controller.task_store.load_attempt("exec-loop", "attempt-1")
        self.assertEqual((before.rounds_used, before.tool_calls_used), (2, 1))
        checkpoint = self.controller.load_last_checkpoint("exec-loop")
        self.controller.lease_manager.release(lease)

        restarted_controller = HarnessController(self.root)
        restarted_sandboxes = SandboxManager(self.root, self.runtime)
        assessment = Reconciler(
            restarted_controller,
            ToolLedger(self.root),
            restarted_sandboxes,
        ).scan()[0]
        self.assertEqual(assessment.status, RecoveryStatus.RESUMABLE)
        new_lease = restarted_controller.lease_manager.acquire("exec-loop", "worker-2", ttl_seconds=300)
        second_provider = DecisionProvider([{"action": "complete", "result": {"summary": "resumed"}}])
        second_router = ToolRouter(
            build_mvp_registry(),
            PolicyEngine(),
            ToolLedger(self.root),
            restarted_sandboxes,
            platform_tools=frozenset({"read_file", "write_file", "shell_exec"}),
        )
        outcome = AgentLoop(
            restarted_controller,
            ModelGateway(second_provider, max_attempts=1, sleeper=lambda _: None),
            second_router,
        ).run(
            task,
            profile,
            execution_id="exec-loop",
            attempt_id="attempt-1",
            sandbox_id=attempt.sandbox_ref,
            lease=new_lease,
            resume_from=checkpoint,
        )
        self.assertEqual(outcome.status, AgentLoopStatus.COMPLETED)
        after = restarted_controller.task_store.load_attempt("exec-loop", "attempt-1")
        self.assertEqual((after.rounds_used, after.tool_calls_used), (3, 1))
        self.assertEqual((self.sandboxes.handle(attempt.sandbox_ref).workspace / "resume.txt").read_text(), "kept")

    def test_reconciler_flags_unfinished_side_effect(self) -> None:
        """验证不确定副作用分类，确保 Harness Reconciler 禁止自动重放 write_file。"""
        profile = self.make_profile()
        task = self.make_task(profile)
        lease, _, attempt = self.prepare(task)
        self.controller.write_checkpoint(
            "exec-loop",
            "attempt-1",
            lease,
            stage="model_response",
            state={"messages": [], "pending_decision": None},
        )
        call = ToolCall(
            "crashed-call",
            "exec-loop",
            "attempt-1",
            "write_file",
            {"path": "a.txt", "content": "x"},
            "crashed-side-effect",
        )
        self.ledger.begin(call)
        self.controller.lease_manager.release(lease)
        assessment = Reconciler(self.controller, self.ledger, self.sandboxes).scan()[0]
        self.assertEqual(assessment.status, RecoveryStatus.NEEDS_RECONCILIATION)

    def test_reconciler_marks_expired_lease_as_resumable(self) -> None:
        """验证 Lease 过期接管，确保 Harness Reconciler 只恢复失去活动所有者的 Execution。"""
        profile = self.make_profile()
        task = self.make_task(profile)
        lease, _, _ = self.prepare(task)
        self.controller.write_checkpoint(
            "exec-loop",
            "attempt-1",
            lease,
            stage="tool_result",
            state={"messages": [], "pending_decision": None},
        )
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        assessment = Reconciler(
            self.controller,
            self.ledger,
            self.sandboxes,
            clock=lambda: future,
        ).scan()[0]
        self.assertEqual(assessment.status, RecoveryStatus.RESUMABLE)
        self.assertTrue(assessment.lease_expired)

    def test_reconciler_discovers_orphan_sandbox(self) -> None:
        """验证孤儿扫描，确保 Harness Reconciler 能发现没有 Execution 的运行容器。"""
        orphan = self.sandboxes.create("orphan-exec", "orphan-attempt")
        reconciler = Reconciler(self.controller, self.ledger, self.sandboxes)
        orphans = reconciler.find_orphan_sandboxes()
        self.assertEqual([item.sandbox_id for item in orphans], [orphan.sandbox_id])


if __name__ == "__main__":
    unittest.main()
