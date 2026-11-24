"""Agent-Harness 单任务端到端运行器。

该模块位于 GraphFlow Handler 与 Agent Loop 之间，串联 Task 注册、租约、Sandbox、
多轮执行、确定性验收、产物收集和 TaskResult 提交，是 MVP 的主执行入口。
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from scheduler.runs import relative_to_root

from .agent_loop import AgentLoop, AgentLoopOutcome, AgentLoopStatus
from .budget import BudgetMeter
from .cancellation import CancellationToken
from .contracts import (
    ArtifactRef,
    AttemptStatus,
    EvidenceRef,
    Task,
    TaskResult,
    TaskResultStatus,
    TaskExecutionStatus,
    UsageSummary,
)
from .controller import HarnessController
from .errors import BudgetExceededError, InfrastructureUnavailableError, WorkspaceConflictError, WorkspacePreparationError
from .errors import AlreadyExistsError
from .integration import AgentProfile
from .persistence import ResultStore
from .sandbox import SandboxArtifactManifest, SandboxManager
from .validation import DeterministicValidator, ValidationCheck, ValidationReport
from .workspace import ChangeSet, WorkspaceManager, WorkspaceSession
from .worker import LeaseHeartbeat
from .state_machine import ATTEMPT_TERMINAL_STATUSES, EXECUTION_TERMINAL_STATUSES


class HarnessTaskRunner:
    """运行单 Task/单 Agent/单 Sandbox，位于 Harness MVP 的应用编排层。"""

    def __init__(
        self,
        root: Path,
        controller: HarnessController,
        agent_loop: AgentLoop,
        validator: DeterministicValidator,
        sandboxes: SandboxManager,
        *,
        result_store: ResultStore | None = None,
        budget_meter: BudgetMeter | None = None,
        workspace_manager: WorkspaceManager | None = None,
    ) -> None:
        self.root = root.resolve()
        self._controller = controller
        self._loop = agent_loop
        self._validator = validator
        self._sandboxes = sandboxes
        self._results = result_store or ResultStore(root)
        self._budget = budget_meter or BudgetMeter()
        self._workspaces = workspace_manager or WorkspaceManager(root)

    def run(
        self,
        task: Task,
        profile: AgentProfile,
        *,
        owner_id: str = "harness-task-runner",
        cancellation: CancellationToken | None = None,
    ) -> TaskResult:
        """执行并提交 TaskResult，位于 GraphFlow 节点调用 Harness 的同步主链路。"""
        try:
            self._controller.register_task(task)
        except AlreadyExistsError:
            # CLI 可先登记再运行；Harness 仅复用内容完全一致的不可变 Task，禁止同 ID 覆盖。
            if self._controller.task_store.load_task(task.task_id) != task:
                raise
        execution, attempt = self._controller.create_execution(task.task_id)
        lease_ttl = max(30, task.budget.max_wall_seconds + 120)
        lease = self._controller.lease_manager.acquire(
            execution.execution_id,
            owner_id,
            ttl_seconds=lease_ttl,
        )
        heartbeat = LeaseHeartbeat(
            self._controller.lease_manager,
            lease,
            ttl_seconds=lease_ttl,
        )
        heartbeat.start()
        manifest: SandboxArtifactManifest | None = None
        report: ValidationReport | None = None
        workspace_session: WorkspaceSession | None = None
        changeset: ChangeSet | None = None
        outcome: AgentLoopOutcome
        try:
            # 源码快照先于 Sandbox 启动，确保 Agent 只修改隔离副本并保留可恢复基线。
            workspace_session = self._workspaces.prepare(
                task,
                execution.execution_id,
                attempt.attempt_id,
                self._sandboxes.workspace_path(execution.execution_id, attempt.attempt_id),
            )
            if workspace_session is not None:
                self._controller.event_store.append(
                    execution.execution_id,
                    "workspace.snapshot_created",
                    attempt_id=attempt.attempt_id,
                    payload={"snapshot_id": workspace_session.snapshot_id},
                )
            execution, attempt = self._controller.start_execution(
                execution.execution_id,
                lease,
                self._sandboxes,
            )
            if not attempt.sandbox_ref:
                raise RuntimeError("started Attempt is missing sandbox_ref")
            outcome = self._loop.run(
                task,
                profile,
                execution_id=execution.execution_id,
                attempt_id=attempt.attempt_id,
                sandbox_id=attempt.sandbox_ref,
                lease=lease,
                cancellation=cancellation,
            )
            if outcome.status == AgentLoopStatus.COMPLETED and outcome.completion is not None:
                self._controller.enter_validation(execution.execution_id, lease)
                report = self._validator.validate(
                    task,
                    profile,
                    outcome.completion,
                    execution_id=execution.execution_id,
                    attempt_id=attempt.attempt_id,
                    sandbox_id=attempt.sandbox_ref,
                )
                report = self._commit_validation_budget(
                    task,
                    execution.execution_id,
                    attempt.attempt_id,
                    lease,
                    report,
                )
                if report.status == "passed" and workspace_session is not None:
                    try:
                        changeset = self._workspaces.create_changeset(workspace_session)
                        self._controller.event_store.append(
                            execution.execution_id,
                            "workspace.changeset_created",
                            attempt_id=attempt.attempt_id,
                            payload={"files": len(changeset.created + changeset.modified + changeset.deleted)},
                        )
                        if task.workspace.writeback == "auto_after_validation":
                            changeset = self._workspaces.apply(workspace_session, changeset)
                            self._controller.event_store.append(
                                execution.execution_id,
                                "workspace.writeback_completed",
                                attempt_id=attempt.attempt_id,
                                payload={"snapshot_id": changeset.snapshot_id},
                            )
                    except (WorkspaceConflictError, WorkspacePreparationError, OSError) as exc:
                        report = ValidationReport(
                            "failed",
                            report.checks + (ValidationCheck(
                                "workspace-writeback",
                                "workspace",
                                False,
                                str(exc),
                                {"error_type": type(exc).__name__},
                            ),),
                            report.tool_calls_used,
                            report.created_at,
                        )
                        outcome = replace(outcome, error_code="workspace_conflict", error_message=str(exc))
                        self._controller.event_store.append(
                            execution.execution_id,
                            "workspace.writeback_failed",
                            attempt_id=attempt.attempt_id,
                            payload={"error_type": type(exc).__name__},
                        )
                validation_path = self._results.write_validation(
                    execution.execution_id,
                    report.to_dict(),
                )
                self._controller.event_store.append(
                    execution.execution_id,
                    "validation.completed",
                    attempt_id=attempt.attempt_id,
                    payload={
                        "status": report.status,
                        "checks": len(report.checks),
                        "failed_checks": sum(1 for check in report.checks if not check.passed),
                    },
                )
                passed = report.status == "passed"
                error = None if passed else "deterministic validation failed"
                execution, attempt, manifest = self._controller.finish_validation(
                    execution.execution_id,
                    attempt.attempt_id,
                    lease,
                    self._sandboxes,
                    passed=passed,
                    error=error,
                )
            else:
                validation_path = None
                if outcome.status == AgentLoopStatus.PAUSED:
                    # 暂停保留 Checkpoint 与 Sandbox，由 task-resume/RecoveryWorker 继续同一 Attempt。
                    execution = self._controller.pause_execution(execution.execution_id, lease)
                    attempt = self._controller.task_store.load_attempt(execution.execution_id, attempt.attempt_id)
                    manifest = None
                else:
                    execution, attempt, manifest = self._controller.finalize_execution(
                        execution.execution_id,
                        attempt.attempt_id,
                        lease,
                        self._sandboxes,
                        outcome=outcome.status.value,
                        error=outcome.error_message,
                    )
            result = self._build_result(
                task,
                profile,
                outcome,
                attempt,
                manifest,
                report,
                validation_path,
                changeset,
            )
            result_path = self._results.write_result(result)
            self._controller.attach_result_ref(
                execution.execution_id,
                relative_to_root(self.root, result_path),
                lease,
            )
            return result
        except InfrastructureUnavailableError as exc:
            # Sandbox 创建已由 Controller 失败关闭；Runner 仍提交标准结果，禁止上层回退宿主执行。
            attempt = self._controller.task_store.load_attempt(
                execution.execution_id,
                attempt.attempt_id,
            )
            outcome = AgentLoopOutcome(
                AgentLoopStatus.FAILED,
                error_code="infrastructure_unavailable",
                error_message=str(exc),
            )
            result = self._build_result(
                task,
                profile,
                outcome,
                attempt,
                None,
                None,
                None,
                changeset,
            )
            result_path = self._results.write_result(result)
            self._controller.attach_result_ref(
                execution.execution_id,
                relative_to_root(self.root, result_path),
                lease,
            )
            return result
        except Exception as exc:
            # 未分类异常必须先收口状态和 Sandbox，再向上抛出，避免留下“仍在运行”的幽灵 Execution。
            self._guard_unexpected_failure(execution.execution_id, attempt.attempt_id, lease, exc)
            raise
        finally:
            heartbeat.stop()
            self._controller.lease_manager.release(lease)

    def resume(
        self,
        execution_id: str,
        profile: AgentProfile,
        *,
        owner_id: str = "harness-resume-worker",
        cancellation: CancellationToken | None = None,
    ) -> TaskResult:
        """从已提交 Checkpoint 继续真实 Agent Loop，位于 Reconciler 判定可恢复后的执行入口。"""
        execution = self._controller.task_store.load_execution(execution_id)
        task = self._controller.task_store.load_task(execution.task_id)
        if execution.active_attempt_id is None:
            raise RuntimeError("resumable Execution is missing active Attempt")
        attempt_id = execution.active_attempt_id
        attempt = self._controller.task_store.load_attempt(execution_id, attempt_id)
        checkpoint = self._controller.load_last_checkpoint(execution_id)
        if checkpoint is None or not attempt.sandbox_ref:
            raise RuntimeError("resumable Execution requires Checkpoint and Sandbox")
        lease_ttl = max(30, task.budget.max_wall_seconds + 120)
        lease = self._controller.lease_manager.acquire(execution_id, owner_id, ttl_seconds=lease_ttl)
        heartbeat = LeaseHeartbeat(self._controller.lease_manager, lease, ttl_seconds=lease_ttl)
        heartbeat.start()
        try:
            self._sandboxes.restore(execution_id, attempt_id, attempt.sandbox_ref)
            if execution.status == TaskExecutionStatus.PAUSED:
                self._controller.resume_execution(execution_id, lease)
            if attempt.status == AttemptStatus.CHECKPOINTED:
                attempt = self._controller.transition_attempt(
                    execution_id,
                    attempt_id,
                    AttemptStatus.RUNNING,
                    lease,
                )
            outcome = self._loop.run(
                task,
                profile,
                execution_id=execution_id,
                attempt_id=attempt_id,
                sandbox_id=attempt.sandbox_ref,
                lease=lease,
                cancellation=cancellation,
                resume_from=checkpoint,
            )
            report: ValidationReport | None = None
            validation_path: Path | None = None
            manifest: SandboxArtifactManifest | None = None
            changeset: ChangeSet | None = None
            if outcome.status == AgentLoopStatus.COMPLETED and outcome.completion is not None:
                self._controller.enter_validation(execution_id, lease)
                report = self._validator.validate(
                    task,
                    profile,
                    outcome.completion,
                    execution_id=execution_id,
                    attempt_id=attempt_id,
                    sandbox_id=attempt.sandbox_ref,
                )
                report = self._commit_validation_budget(task, execution_id, attempt_id, lease, report)
                session = self._workspaces.restore(execution_id) if task.workspace.mode != "none" else None
                if report.status == "passed" and session is not None:
                    changeset = self._workspaces.create_changeset(session)
                    if task.workspace.writeback == "auto_after_validation":
                        changeset = self._workspaces.apply(session, changeset)
                validation_path = self._results.write_validation(execution_id, report.to_dict())
                _, attempt, manifest = self._controller.finish_validation(
                    execution_id,
                    attempt_id,
                    lease,
                    self._sandboxes,
                    passed=report.status == "passed",
                    error=None if report.status == "passed" else "deterministic validation failed",
                )
            else:
                if outcome.status == AgentLoopStatus.PAUSED:
                    self._controller.pause_execution(execution_id, lease)
                    attempt = self._controller.task_store.load_attempt(execution_id, attempt_id)
                else:
                    _, attempt, manifest = self._controller.finalize_execution(
                        execution_id,
                        attempt_id,
                        lease,
                        self._sandboxes,
                        outcome=outcome.status.value,
                        error=outcome.error_message,
                    )
            result = self._build_result(
                task,
                profile,
                outcome,
                attempt,
                manifest,
                report,
                validation_path,
                changeset,
            )
            result_path = self._results.write_result(result)
            self._controller.attach_result_ref(execution_id, relative_to_root(self.root, result_path), lease)
            return result
        except Exception as exc:
            self._guard_unexpected_failure(execution_id, attempt_id, lease, exc)
            raise
        finally:
            heartbeat.stop()
            self._controller.lease_manager.release(lease)

    def _guard_unexpected_failure(self, execution_id: str, attempt_id: str, lease, error: Exception) -> None:
        """尽力失败化状态并回收资源，位于 Runner 最外层异常边界。"""
        message = f"{type(error).__name__}: {error}"
        try:
            attempt = self._controller.task_store.load_attempt(execution_id, attempt_id)
            if attempt.sandbox_ref:
                try:
                    self._sandboxes.destroy(attempt.sandbox_ref)
                except Exception:
                    # 清理失败写事件后保留原异常，供 Reconciler 后续治理孤儿资源。
                    self._controller.event_store.append(
                        execution_id,
                        "sandbox.cleanup_failed",
                        attempt_id=attempt_id,
                        payload={"error_type": type(error).__name__},
                    )
            if attempt.status not in ATTEMPT_TERMINAL_STATUSES:
                self._controller.transition_attempt(
                    execution_id,
                    attempt_id,
                    AttemptStatus.FAILED,
                    lease,
                    last_error=message,
                )
            execution = self._controller.task_store.load_execution(execution_id)
            if execution.status == TaskExecutionStatus.CREATED:
                self._controller.transition_execution(execution_id, TaskExecutionStatus.VALIDATING, lease)
                execution = self._controller.task_store.load_execution(execution_id)
            if execution.status not in EXECUTION_TERMINAL_STATUSES:
                self._controller.transition_execution(
                    execution_id,
                    TaskExecutionStatus.FAILED,
                    lease,
                    last_error=message,
                )
            self._controller.event_store.append(
                execution_id,
                "execution.unexpected_failure",
                attempt_id=attempt_id,
                payload={"error_type": type(error).__name__},
            )
        except Exception:
            # Guard 自身不能覆盖原始异常；持久化残留将由启动期 Reconciler 继续处理。
            return

    def _commit_validation_budget(
        self,
        task: Task,
        execution_id: str,
        attempt_id: str,
        lease,
        report: ValidationReport,
    ) -> ValidationReport:
        for _ in range(report.tool_calls_used):
            self._controller.record_tool_usage(execution_id, attempt_id, lease)
        attempt = self._controller.task_store.load_attempt(execution_id, attempt_id)
        try:
            self._budget.check(task, attempt)
            return report
        except BudgetExceededError as exc:
            check = ValidationCheck(
                "validation-budget",
                "budget",
                False,
                str(exc),
                {"dimension": exc.dimension},
            )
            return ValidationReport(
                "failed",
                report.checks + (check,),
                report.tool_calls_used,
                report.created_at,
            )

    def _build_result(
        self,
        task: Task,
        profile: AgentProfile,
        outcome: AgentLoopOutcome,
        attempt,
        manifest: SandboxArtifactManifest | None,
        report: ValidationReport | None,
        validation_path: Path | None,
        changeset: ChangeSet | None = None,
    ) -> TaskResult:
        passed = outcome.status == AgentLoopStatus.COMPLETED and report is not None and report.status == "passed"
        status = self._result_status(outcome, passed)
        artifacts = self._artifact_refs(attempt.sandbox_ref, manifest, changeset)
        evidence = self._evidence_refs(
            attempt.execution_id,
            attempt.attempt_id,
            validation_path,
            manifest,
            changeset,
        )
        completion = outcome.completion or {}
        summary = str(completion.get("summary") or (
            "Task completed and validated" if passed else outcome.error_message or "Task failed"
        ))
        failure_kind = None if passed else self._failure_kind(outcome, report)
        return TaskResult(
            task_id=task.task_id,
            execution_id=attempt.execution_id,
            attempt_id=attempt.attempt_id,
            status=status,
            summary=summary,
            usage=UsageSummary(
                rounds=attempt.rounds_used,
                tool_calls=attempt.tool_calls_used,
                model_tokens=attempt.model_tokens_used,
                cost=attempt.cost_used,
                wall_seconds=self._budget.wall_seconds(attempt),
            ),
            artifacts=artifacts,
            evidence=evidence,
            validation={} if report is None else report.to_dict(),
            changeset={} if changeset is None else changeset.to_dict(),
            version_set={
                **task.version_set,
                "agent_profile": f"{profile.agent_id}@{profile.version}",
                "agent_profile_hash": profile.content_hash,
                "model": profile.model,
                "policy": task.version_set.get("policy", "mvp-policy-v1"),
                "runtime": type(self._sandboxes).__name__,
                "workspace_snapshot": "none" if changeset is None else changeset.snapshot_id,
                "harness": "mvp-0.7",
                "task_result_schema": "1.0",
            },
            error_code=None if passed else outcome.error_code or "validation_failed",
            error_message=None if passed else outcome.error_message or "deterministic validation failed",
            failure_kind=failure_kind,
            retryable=failure_kind in {"validation", "model", "budget", "timeout"},
        )

    def _artifact_refs(
        self,
        sandbox_id: str | None,
        manifest: SandboxArtifactManifest | None,
        changeset: ChangeSet | None = None,
    ) -> tuple[ArtifactRef, ...]:
        refs: list[ArtifactRef] = []
        if changeset is not None:
            patch_path = self.root / changeset.patch_ref
            refs.append(ArtifactRef(
                "changeset-patch",
                changeset.patch_ref,
                changeset.patch_sha256,
                patch_path.stat().st_size,
            ))
        if manifest is None or sandbox_id is None:
            return tuple(refs)
        workspace = (
            self.root / ".loop" / "harness" / "sandboxes" /
            manifest.execution_id / manifest.attempt_id / "workspace"
        )
        refs.extend(
            ArtifactRef(
                "sandbox-file",
                relative_to_root(self.root, workspace / str(item["path"])),
                str(item["sha256"]),
                int(item["size"]),
            )
            for item in manifest.files
        )
        return tuple(refs)

    def _evidence_refs(
        self,
        execution_id: str,
        attempt_id: str,
        validation_path: Path | None,
        manifest: SandboxArtifactManifest | None,
        changeset: ChangeSet | None = None,
    ) -> tuple[EvidenceRef, ...]:
        refs: list[EvidenceRef] = []
        if validation_path is not None:
            refs.append(self._evidence("validation-report", validation_path, "确定性验收报告"))
        if manifest is not None:
            manifest_path = (
                self.root / ".loop" / "harness" / "executions" / execution_id /
                "attempts" / attempt_id / "sandbox-artifacts.json"
            )
            refs.append(self._evidence("artifact-manifest", manifest_path, "Sandbox 产物哈希清单"))
        if changeset is not None:
            changeset_path = self.root / ".loop" / "harness" / "executions" / execution_id / "changeset.json"
            refs.append(self._evidence("changeset", changeset_path, "Agent 文件变更与安全回写记录"))
        return tuple(refs)

    def _evidence(self, evidence_type: str, path: Path, description: str) -> EvidenceRef:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return EvidenceRef(
            evidence_type,
            relative_to_root(self.root, path),
            digest,
            description,
        )

    @staticmethod
    def _result_status(outcome: AgentLoopOutcome, passed: bool) -> TaskResultStatus:
        if passed:
            return TaskResultStatus.SUCCEEDED
        return {
            AgentLoopStatus.CANCELLED: TaskResultStatus.CANCELLED,
            AgentLoopStatus.PAUSED: TaskResultStatus.NEEDS_RECONCILIATION,
            AgentLoopStatus.TIMED_OUT: TaskResultStatus.TIMED_OUT,
            AgentLoopStatus.NEEDS_RECONCILIATION: TaskResultStatus.NEEDS_RECONCILIATION,
        }.get(outcome.status, TaskResultStatus.FAILED)

    @staticmethod
    def _failure_kind(outcome: AgentLoopOutcome, report: ValidationReport | None) -> str:
        if outcome.error_code == "workspace_conflict":
            return "workspace_conflict"
        if outcome.error_code in {"infrastructure_unavailable", "InfrastructureUnavailableError"}:
            return "infrastructure"
        if outcome.status == AgentLoopStatus.NEEDS_RECONCILIATION:
            return "needs_reconciliation"
        if outcome.status == AgentLoopStatus.PAUSED:
            return "needs_reconciliation"
        if outcome.status == AgentLoopStatus.BUDGET_EXHAUSTED:
            return "budget"
        if outcome.status == AgentLoopStatus.CANCELLED:
            return "cancelled"
        if outcome.status == AgentLoopStatus.TIMED_OUT:
            return "timeout"
        if report is not None and report.status != "passed":
            return "validation"
        return "model"
