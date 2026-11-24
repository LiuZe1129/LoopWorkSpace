"""Agent-Harness 阶段 1 核心领域契约。

该模块位于 GraphFlow Node 与 Harness Runtime 的数据边界，定义不可变 Task、一次
TaskExecution 以及可重试 Attempt；后续 Agent Loop、Sandbox 和 TaskResult 都引用这些 ID。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> str:
    """生成 UTC 时间戳，位于 Harness 状态、事件和 Checkpoint 的统一时间来源。"""
    return datetime.now(timezone.utc).isoformat()


class TaskExecutionStatus(str, Enum):
    """TaskExecution 状态集合，位于 Harness 单 Task 生命周期控制流程。"""

    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    PAUSED = "PAUSED"
    VALIDATING_RESULT = "VALIDATING_RESULT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class AttemptStatus(str, Enum):
    """Attempt 状态集合，位于 Harness 每次具体执行尝试和恢复流程。"""

    PENDING = "PENDING"
    PREPARING_SANDBOX = "PREPARING_SANDBOX"
    RUNNING = "RUNNING"
    CHECKPOINTED = "CHECKPOINTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"


class TaskResultStatus(str, Enum):
    """TaskResult 终态集合，位于 Harness 结果映射回 GraphFlow 的边界。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    NEEDS_RECONCILIATION = "needs_reconciliation"


@dataclass(frozen=True)
class AgentProfileRef:
    """引用版本化 AgentProfile，位于 Task 绑定单 Agent 能力的流程边界。"""

    agent_id: str
    version: str
    content_hash: str

    def to_dict(self) -> dict[str, str]:
        """序列化 AgentProfile 引用，供 Harness TaskStore 持久化版本闭包。"""
        return {
            "agent_id": self.agent_id,
            "version": self.version,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentProfileRef":
        """恢复 AgentProfile 引用，位于 Harness 从持久化 Task 重建执行上下文的环节。"""
        return cls(
            agent_id=str(payload["agent_id"]),
            version=str(payload["version"]),
            content_hash=str(payload["content_hash"]),
        )


@dataclass(frozen=True)
class TaskPermissions:
    """Task 权限快照，位于 Harness 后续 Policy Engine 计算权限交集的输入边界。"""

    tools: tuple[str, ...]
    workspace_root: str = "workspace"
    network: str = "deny"

    def to_dict(self) -> dict[str, Any]:
        """序列化权限快照，供 Harness 在整个 Execution 中固定授权范围。"""
        return {
            "tools": list(self.tools),
            "workspace_root": self.workspace_root,
            "network": self.network,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskPermissions":
        """恢复权限快照，位于 Harness Execution 启动前的授权上下文装配环节。"""
        return cls(
            tools=tuple(str(item) for item in payload.get("tools", []) or []),
            workspace_root=str(payload.get("workspace_root", "workspace")),
            network=str(payload.get("network", "deny")),
        )


@dataclass(frozen=True)
class TaskBudget:
    """Task 资源预算，位于 Harness Agent Loop 和 Tool 执行前的限制输入。"""

    max_rounds: int = 20
    max_tool_calls: int = 50
    max_wall_seconds: int = 900
    max_model_tokens: int = 100_000
    max_cost: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化预算，供 Harness 在恢复和重试时保留原始资源上限。"""
        return {
            "max_rounds": self.max_rounds,
            "max_tool_calls": self.max_tool_calls,
            "max_wall_seconds": self.max_wall_seconds,
            "max_model_tokens": self.max_model_tokens,
            "max_cost": self.max_cost,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskBudget":
        """恢复预算，位于 Harness Execution 创建和 Checkpoint 恢复的控制环节。"""
        max_cost = payload.get("max_cost")
        return cls(
            max_rounds=int(payload.get("max_rounds", 20)),
            max_tool_calls=int(payload.get("max_tool_calls", 50)),
            max_wall_seconds=int(payload.get("max_wall_seconds", 900)),
            max_model_tokens=int(payload.get("max_model_tokens", 100_000)),
            max_cost=None if max_cost is None else float(max_cost),
        )


@dataclass(frozen=True)
class WorkspaceConfig:
    """定义本地工程快照策略，位于 Harness Task 输入与隔离 Workspace 创建边界。"""

    mode: str = "local_snapshot"
    source_root: str = "."
    include_untracked: bool = True
    writeback: str = "auto_after_validation"
    conflict_policy: str = "fail"
    exclude: tuple[str, ...] = (
        ".git/**",
        ".loop/**",
        "**/.env",
        "**/.env.*",
        "**/node_modules/**",
        "**/__pycache__/**",
        "**/*.pyc",
    )

    def to_dict(self) -> dict[str, Any]:
        """序列化 Workspace 策略，供 Harness Snapshot、恢复与审计流程固定输入。"""
        return {
            "mode": self.mode,
            "source_root": self.source_root,
            "include_untracked": self.include_untracked,
            "writeback": self.writeback,
            "conflict_policy": self.conflict_policy,
            "exclude": list(self.exclude),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkspaceConfig":
        """恢复并校验 Workspace 策略，位于 Harness Task 载入到源码快照准备环节。"""
        mode = str(payload.get("mode", "local_snapshot"))
        writeback = str(payload.get("writeback", "auto_after_validation"))
        conflict_policy = str(payload.get("conflict_policy", "fail"))
        if mode not in {"local_snapshot", "none"}:
            raise ValueError(f"unsupported workspace mode: {mode}")
        if writeback not in {"none", "manual", "auto_after_validation"}:
            raise ValueError(f"unsupported workspace writeback: {writeback}")
        if conflict_policy != "fail":
            raise ValueError("MVP only supports conflict_policy=fail")
        defaults = cls()
        return cls(
            mode=mode,
            source_root=str(payload.get("source_root", ".")),
            include_untracked=bool(payload.get("include_untracked", True)),
            writeback=writeback,
            conflict_policy=conflict_policy,
            exclude=tuple(str(item) for item in payload.get("exclude", defaults.exclude) or ()),
        )


@dataclass(frozen=True)
class Task:
    """不可变 Task Contract，位于 GraphFlow Node 提交给 Harness 的入口边界。"""

    task_id: str
    graph_run_id: str
    node_id: str
    objective: str
    agent_profile: AgentProfileRef
    permissions: TaskPermissions
    budget: TaskBudget
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    input_refs: tuple[str, ...] = ()
    acceptance: tuple[dict[str, Any], ...] = ()
    version_set: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """序列化 Task Contract，供 Harness 持久化并向后续执行组件传递。"""
        return {
            "schema_version": "1.0",
            "task_id": self.task_id,
            "source": {"graph_run_id": self.graph_run_id, "node_id": self.node_id},
            "objective": self.objective,
            "agent_profile": self.agent_profile.to_dict(),
            "permissions": self.permissions.to_dict(),
            "budget": self.budget.to_dict(),
            "workspace": self.workspace.to_dict(),
            "input_refs": list(self.input_refs),
            "acceptance": list(self.acceptance),
            "version_set": dict(self.version_set),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Task":
        """恢复 Task Contract，位于 Harness 服务重启后重新装载任务的流程环节。"""
        source = payload.get("source", {}) or {}
        return cls(
            task_id=str(payload["task_id"]),
            graph_run_id=str(source["graph_run_id"]),
            node_id=str(source["node_id"]),
            objective=str(payload["objective"]),
            agent_profile=AgentProfileRef.from_dict(payload["agent_profile"]),
            permissions=TaskPermissions.from_dict(payload.get("permissions", {}) or {}),
            budget=TaskBudget.from_dict(payload.get("budget", {}) or {}),
            workspace=WorkspaceConfig.from_dict(payload.get("workspace", {}) or {}),
            input_refs=tuple(str(item) for item in payload.get("input_refs", []) or []),
            acceptance=tuple(dict(item) for item in payload.get("acceptance", []) or []),
            version_set={str(key): str(value) for key, value in (payload.get("version_set", {}) or {}).items()},
            created_at=str(payload.get("created_at") or utc_now()),
        )


@dataclass(frozen=True)
class TaskExecution:
    """一次 Task 运行记录，位于 Harness 生命周期、并发控制和恢复流程的核心。"""

    execution_id: str
    task_id: str
    status: TaskExecutionStatus = TaskExecutionStatus.CREATED
    active_attempt_id: str | None = None
    attempt_ids: tuple[str, ...] = ()
    state_version: int = 0
    checkpoint_ref: str | None = None
    result_ref: str | None = None
    last_error: str | None = None
    cancel_requested_at: str | None = None
    cancellation_reason: str | None = None
    pause_requested_at: str | None = None
    pause_reason: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """序列化 TaskExecution，供 Harness TaskStore 执行版本化持久化。"""
        return {
            "schema_version": "1.0",
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "active_attempt_id": self.active_attempt_id,
            "attempt_ids": list(self.attempt_ids),
            "state_version": self.state_version,
            "checkpoint_ref": self.checkpoint_ref,
            "result_ref": self.result_ref,
            "last_error": self.last_error,
            "cancel_requested_at": self.cancel_requested_at,
            "cancellation_reason": self.cancellation_reason,
            "pause_requested_at": self.pause_requested_at,
            "pause_reason": self.pause_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskExecution":
        """恢复 TaskExecution，位于 Harness Worker 领取任务或服务重启后的恢复环节。"""
        return cls(
            execution_id=str(payload["execution_id"]),
            task_id=str(payload["task_id"]),
            status=TaskExecutionStatus(str(payload["status"])),
            active_attempt_id=payload.get("active_attempt_id"),
            attempt_ids=tuple(str(item) for item in payload.get("attempt_ids", []) or []),
            state_version=int(payload.get("state_version", 0)),
            checkpoint_ref=payload.get("checkpoint_ref"),
            result_ref=payload.get("result_ref"),
            last_error=payload.get("last_error"),
            cancel_requested_at=payload.get("cancel_requested_at"),
            cancellation_reason=payload.get("cancellation_reason"),
            pause_requested_at=payload.get("pause_requested_at"),
            pause_reason=payload.get("pause_reason"),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )


@dataclass(frozen=True)
class Attempt:
    """一次具体执行尝试，位于 Harness 重试、Sandbox 绑定和副作用恢复流程。"""

    attempt_id: str
    execution_id: str
    sequence: int
    status: AttemptStatus = AttemptStatus.PENDING
    state_version: int = 0
    sandbox_ref: str | None = None
    checkpoint_ref: str | None = None
    rounds_used: int = 0
    tool_calls_used: int = 0
    model_tokens_used: int = 0
    cost_used: float | None = 0.0
    last_error: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """序列化 Attempt，供 Harness 记录每次尝试独立预算和恢复状态。"""
        return {
            "schema_version": "1.0",
            "attempt_id": self.attempt_id,
            "execution_id": self.execution_id,
            "sequence": self.sequence,
            "status": self.status.value,
            "state_version": self.state_version,
            "sandbox_ref": self.sandbox_ref,
            "checkpoint_ref": self.checkpoint_ref,
            "usage": {
                "rounds": self.rounds_used,
                "tool_calls": self.tool_calls_used,
                "model_tokens": self.model_tokens_used,
                "cost": self.cost_used,
            },
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Attempt":
        """恢复 Attempt，位于 Harness Checkpoint 恢复或问题诊断的执行环节。"""
        usage = payload.get("usage", {}) or {}
        return cls(
            attempt_id=str(payload["attempt_id"]),
            execution_id=str(payload["execution_id"]),
            sequence=int(payload["sequence"]),
            status=AttemptStatus(str(payload["status"])),
            state_version=int(payload.get("state_version", 0)),
            sandbox_ref=payload.get("sandbox_ref"),
            checkpoint_ref=payload.get("checkpoint_ref"),
            rounds_used=int(usage.get("rounds", 0)),
            tool_calls_used=int(usage.get("tool_calls", 0)),
            model_tokens_used=int(usage.get("model_tokens", 0)),
            cost_used=None if usage.get("cost") is None else float(usage.get("cost", 0.0)),
            last_error=payload.get("last_error"),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )


@dataclass(frozen=True)
class UsageSummary:
    """汇总 Task 用量，位于 Harness Attempt 状态到最终 TaskResult 的转换环节。"""

    rounds: int
    tool_calls: int
    model_tokens: int
    cost: float | None
    wall_seconds: float

    def to_dict(self) -> dict[str, Any]:
        """序列化用量，供 Harness TaskResult、GraphFlow Artifact 和评测流程使用。"""
        return {
            "rounds": self.rounds,
            "tool_calls": self.tool_calls,
            "model_tokens": self.model_tokens,
            "cost": self.cost,
            "wall_seconds": self.wall_seconds,
        }


@dataclass(frozen=True)
class ArtifactRef:
    """引用任务产物，位于 Harness Sandbox 收集到 GraphFlow Artifact 映射的边界。"""

    artifact_type: str
    path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """序列化产物引用，供 Harness TaskResult 保留可验证文件证据。"""
        return {
            "type": self.artifact_type,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class EvidenceRef:
    """引用验证证据，位于 Harness Validator 输出到 GraphFlow 审计流程的边界。"""

    evidence_type: str
    path: str
    sha256: str
    description: str

    def to_dict(self) -> dict[str, str]:
        """序列化证据引用，供 Harness TaskResult 与 EvidenceManifest 消费。"""
        return {
            "type": self.evidence_type,
            "path": self.path,
            "sha256": self.sha256,
            "description": self.description,
        }


@dataclass(frozen=True)
class TaskResult:
    """标准化 Harness 最终结果，位于单 Agent 执行内核与 GraphFlow 的唯一输出边界。"""

    task_id: str
    execution_id: str
    attempt_id: str
    status: TaskResultStatus
    summary: str
    usage: UsageSummary
    artifacts: tuple[ArtifactRef, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    validation: dict[str, Any] = field(default_factory=dict)
    # ChangeSet 位于确定性验收与本地工程回写之间，用于审计 Agent 实际文件副作用。
    changeset: dict[str, Any] = field(default_factory=dict)
    version_set: dict[str, str] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    failure_kind: str | None = None
    retryable: bool = False
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """序列化最终结果，供 Harness ResultStore 和 GraphFlow Handler 稳定消费。"""
        return {
            "schema_version": "1.0",
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "status": self.status.value,
            "summary": self.summary,
            "usage": self.usage.to_dict(),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "evidence": [item.to_dict() for item in self.evidence],
            "validation": dict(self.validation),
            "changeset": dict(self.changeset),
            "version_set": dict(self.version_set),
            "error": None
            if self.error_code is None and self.error_message is None
            else {"code": self.error_code, "message": self.error_message},
            "failure_kind": self.failure_kind,
            "retryable": self.retryable,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskResult":
        """恢复最终结果，位于 Harness 重启后 GraphFlow 查询 Execution 结果的环节。"""
        usage = payload.get("usage", {}) or {}
        error = payload.get("error") or {}
        return cls(
            task_id=str(payload["task_id"]),
            execution_id=str(payload["execution_id"]),
            attempt_id=str(payload["attempt_id"]),
            status=TaskResultStatus(str(payload["status"])),
            summary=str(payload["summary"]),
            usage=UsageSummary(
                rounds=int(usage.get("rounds", 0)),
                tool_calls=int(usage.get("tool_calls", 0)),
                model_tokens=int(usage.get("model_tokens", 0)),
                cost=None if usage.get("cost") is None else float(usage["cost"]),
                wall_seconds=float(usage.get("wall_seconds", 0.0)),
            ),
            artifacts=tuple(
                ArtifactRef(str(item["type"]), str(item["path"]), str(item["sha256"]), int(item["size_bytes"]))
                for item in payload.get("artifacts", []) or []
            ),
            evidence=tuple(
                EvidenceRef(str(item["type"]), str(item["path"]), str(item["sha256"]), str(item["description"]))
                for item in payload.get("evidence", []) or []
            ),
            validation=dict(payload.get("validation", {}) or {}),
            changeset=dict(payload.get("changeset", {}) or {}),
            version_set={str(key): str(value) for key, value in (payload.get("version_set", {}) or {}).items()},
            error_code=error.get("code"),
            error_message=error.get("message"),
            failure_kind=payload.get("failure_kind"),
            retryable=bool(payload.get("retryable", False)),
            created_at=str(payload["created_at"]),
        )
