"""验证 Agent-Harness 阶段 1 的契约、状态、持久化、租约和恢复基础设施。"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from harness import AttemptStatus, HarnessController, TaskExecutionStatus
from harness.contracts import AgentProfileRef, Task, TaskBudget, TaskPermissions
from harness.errors import (
    AlreadyExistsError,
    InvalidTransitionError,
    LeaseConflictError,
    LeaseOwnershipError,
    VersionConflictError,
)
from harness.persistence import (
    Checkpoint,
    CheckpointStore,
    EventStore,
    ExecutionLeaseManager,
    TaskStore,
)
from harness.state_machine import transition_attempt, transition_execution


class MutableClock:
    """为 Harness Lease 测试提供可控时钟，避免依赖真实等待。"""

    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        """返回测试时间，模拟 Harness Worker 获取当前租约时刻。"""
        return self.value

    def advance(self, seconds: int) -> None:
        """推进测试时间，模拟 Harness Execution Lease 到期和故障接管。"""
        self.value += timedelta(seconds=seconds)


class HarnessStageOneTests(unittest.TestCase):
    """覆盖阶段 1 基础设施，确认 Harness 可持久化、可冲突检测和可恢复。"""

    def setUp(self) -> None:
        """创建隔离目录，位于 Harness 文件持久化测试的准备环节。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        """回收隔离目录，位于 Harness 文件持久化测试的收尾环节。"""
        self.temporary.cleanup()

    def make_task(self, task_id: str = "task-1") -> Task:
        """构造标准 Task，位于 GraphFlow 向 Harness 提交契约的测试边界。"""
        return Task(
            task_id=task_id,
            graph_run_id="run-1",
            node_id="node-1",
            objective="完成单 Agent 测试任务",
            agent_profile=AgentProfileRef("agent-1", "v1", "sha256:abc"),
            permissions=TaskPermissions(("read_file", "write_file")),
            budget=TaskBudget(max_rounds=5),
            version_set={"prompt": "v1"},
        )

    def test_task_contract_round_trip(self) -> None:
        """验证 Task 序列化闭包，确保 Harness 重启后契约语义不丢失。"""
        original = self.make_task()
        restored = Task.from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_state_machines_accept_and_reject_transitions(self) -> None:
        """验证合法和非法状态跳转，确保 Harness 终态不能被重新启动。"""
        controller = HarnessController(self.root)
        controller.register_task(self.make_task())
        execution, attempt = controller.create_execution("task-1", execution_id="exec-1", attempt_id="attempt-1")
        validating = transition_execution(execution, TaskExecutionStatus.VALIDATING)
        self.assertEqual(validating.status, TaskExecutionStatus.VALIDATING)
        preparing = transition_attempt(attempt, AttemptStatus.PREPARING_SANDBOX)
        self.assertEqual(preparing.status, AttemptStatus.PREPARING_SANDBOX)
        with self.assertRaises(InvalidTransitionError):
            transition_execution(execution, TaskExecutionStatus.SUCCEEDED)
        with self.assertRaises(InvalidTransitionError):
            transition_attempt(attempt, AttemptStatus.SUCCEEDED)

    def test_task_store_cas_detects_stale_writer(self) -> None:
        """验证 state_version CAS，确保 Harness 并发 Worker 不会静默覆盖状态。"""
        store = TaskStore(self.root)
        store.create_task(self.make_task())
        controller = HarnessController(self.root)
        execution, _ = controller.create_execution("task-1", execution_id="exec-1", attempt_id="attempt-1")
        changed = transition_execution(execution, TaskExecutionStatus.VALIDATING)
        saved = store.save_execution(changed, expected_version=0)
        self.assertEqual(saved.state_version, 1)
        with self.assertRaises(VersionConflictError):
            store.save_execution(changed, expected_version=0)

    def test_interrupted_atomic_replace_keeps_previous_json(self) -> None:
        """模拟替换中断，确保 Harness 状态写入失败时旧 JSON 仍完整可恢复。"""
        store = TaskStore(self.root)
        store.create_task(self.make_task())
        controller = HarnessController(self.root)
        execution, _ = controller.create_execution(
            "task-1",
            execution_id="exec-1",
            attempt_id="attempt-1",
        )
        changed = transition_execution(execution, TaskExecutionStatus.VALIDATING)
        with patch("harness.persistence.atomic.os.replace", side_effect=OSError("simulated crash")):
            with self.assertRaises(OSError):
                store.save_execution(changed, expected_version=0)
        restored = store.load_execution("exec-1")
        self.assertEqual(restored.status, TaskExecutionStatus.CREATED)
        self.assertEqual(restored.state_version, 0)

    def test_duplicate_records_and_unsafe_ids_are_rejected(self) -> None:
        """验证重复和越界标识被拒绝，确保 Harness 存储边界不覆盖或逃逸。"""
        store = TaskStore(self.root)
        task = self.make_task()
        store.create_task(task)
        with self.assertRaises(AlreadyExistsError):
            store.create_task(task)
        with self.assertRaises(ValueError):
            store.load_task("../outside")

    def test_event_store_sequence_survives_recreation(self) -> None:
        """验证事件序号跨实例连续，确保 Harness 重启后时间线仍可审计。"""
        first_store = EventStore(self.root)
        first_store.append("exec-1", "execution.created")
        second_store = EventStore(self.root)
        second_store.append("exec-1", "execution.status_changed")
        events = first_store.read("exec-1")
        self.assertEqual([event.sequence for event in events], [1, 2])

    def test_lease_conflict_expiry_and_stale_release(self) -> None:
        """验证租约接管及旧令牌隔离，确保 Harness 单 Execution 只有一个写者。"""
        clock = MutableClock()
        leases = ExecutionLeaseManager(self.root, clock=clock)
        first = leases.acquire("exec-1", "worker-1", ttl_seconds=10)
        with self.assertRaises(LeaseConflictError):
            leases.acquire("exec-1", "worker-2", ttl_seconds=10)
        clock.advance(11)
        second = leases.acquire("exec-1", "worker-2", ttl_seconds=10)
        with self.assertRaises(LeaseOwnershipError):
            leases.release(first)
        self.assertEqual(leases.inspect("exec-1"), second)
        leases.release(second)
        self.assertIsNone(leases.inspect("exec-1"))

    def test_checkpoint_is_immutable(self) -> None:
        """验证 Checkpoint 不可覆盖，确保 Harness 恢复点具备稳定审计语义。"""
        store = CheckpointStore(self.root)
        checkpoint = Checkpoint("cp-1", "exec-1", "attempt-1", "tool_result", {"round": 1})
        store.write(checkpoint)
        self.assertEqual(store.load("exec-1", "cp-1"), checkpoint)
        with self.assertRaises(AlreadyExistsError):
            store.write(replace(checkpoint, state={"round": 2}))

    def test_controller_requires_lease_and_restores_state(self) -> None:
        """验证 Controller 串联完整基础流程，确保 Harness 重启可恢复状态和事件。"""
        controller = HarnessController(self.root)
        controller.register_task(self.make_task())
        controller.create_execution("task-1", execution_id="exec-1", attempt_id="attempt-1")
        lease = controller.lease_manager.acquire("exec-1", "worker-1")
        controller.transition_execution("exec-1", TaskExecutionStatus.VALIDATING, lease)
        controller.transition_execution("exec-1", TaskExecutionStatus.RUNNING, lease)
        controller.transition_attempt("exec-1", "attempt-1", AttemptStatus.PREPARING_SANDBOX, lease)
        controller.transition_attempt(
            "exec-1",
            "attempt-1",
            AttemptStatus.RUNNING,
            lease,
            sandbox_ref="sandbox-1",
        )
        controller.write_checkpoint(
            "exec-1",
            "attempt-1",
            lease,
            stage="model_response",
            state={"round": 1},
            checkpoint_id="cp-1",
        )

        restarted = HarnessController(self.root)
        execution = restarted.task_store.load_execution("exec-1")
        attempt = restarted.task_store.load_attempt("exec-1", "attempt-1")
        self.assertEqual(execution.checkpoint_ref, "cp-1")
        self.assertEqual(attempt.status, AttemptStatus.CHECKPOINTED)
        self.assertEqual(attempt.sandbox_ref, "sandbox-1")
        self.assertEqual(len(restarted.event_store.read("exec-1")), 6)

    def test_controller_audits_rejected_transition(self) -> None:
        """验证非法迁移写入事件，确保 Harness 拒绝动作仍可被完整审计。"""
        controller = HarnessController(self.root)
        controller.register_task(self.make_task())
        controller.create_execution("task-1", execution_id="exec-1", attempt_id="attempt-1")
        lease = controller.lease_manager.acquire("exec-1", "worker-1")
        with self.assertRaises(InvalidTransitionError):
            controller.transition_execution("exec-1", TaskExecutionStatus.SUCCEEDED, lease)
        events = controller.event_store.read("exec-1")
        self.assertEqual(events[-1].event_type, "execution.transition_rejected")
        self.assertEqual(events[-1].payload, {"from": "CREATED", "to": "SUCCEEDED"})

    def test_retry_creates_new_attempt(self) -> None:
        """验证重试创建新 Attempt，确保 Harness 不覆盖原尝试的状态和审计记录。"""
        controller = HarnessController(self.root)
        controller.register_task(self.make_task())
        controller.create_execution("task-1", execution_id="exec-1", attempt_id="attempt-1")
        lease = controller.lease_manager.acquire("exec-1", "worker-1")
        controller.transition_attempt("exec-1", "attempt-1", AttemptStatus.FAILED, lease)
        execution, second = controller.create_attempt("exec-1", lease, attempt_id="attempt-2")
        self.assertEqual(execution.attempt_ids, ("attempt-1", "attempt-2"))
        self.assertEqual(second.sequence, 2)
        self.assertEqual(len(controller.task_store.list_attempts("exec-1")), 2)


if __name__ == "__main__":
    unittest.main()
