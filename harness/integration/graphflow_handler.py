"""GraphFlow 节点到 Agent-Harness Task 的适配器。

该模块位于现有 DRGExecutor 与 HarnessTaskRunner 的边界，只负责契约转换和
TaskResult 映射；GraphFlow 不读取模型消息、ToolCall 或 Sandbox 内部状态。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Protocol

from scheduler.runs import RunState, relative_to_root, utc_now, write_json
from scheduler.gate_auto import build_harness_acceptance
from scheduler.tool_contracts import DEFAULT_TOOL_CONTRACTS

from ..contracts import Task, TaskBudget, TaskPermissions, TaskResult, TaskResultStatus
from ..task_runner import HarnessTaskRunner
from .agent_profile import AgentProfile


class TaskRunner(Protocol):
    """约束 GraphFlow 可调用的 Harness 入口，位于编排层与执行内核的边界。"""

    def run(self, task: Task, profile: AgentProfile) -> TaskResult:
        """执行单 Task 并返回稳定结果契约，供 GraphFlow Handler 消费。"""
        ...


class HarnessNodeHandler:
    """把 GraphFlow 节点映射为 Task，位于 DRGExecutor 的真实节点执行分支。"""

    def __init__(
        self,
        root: Path,
        runner: TaskRunner | HarnessTaskRunner,
        profile_resolver: Callable[[str], AgentProfile],
    ) -> None:
        self.root = root.resolve()
        self._runner = runner
        self._profiles = profile_resolver

    def __call__(
        self,
        run_id: str,
        graph: dict[str, Any],
        state: RunState,
        node_payload: dict[str, Any],
    ):
        """执行节点并映射 NodeExecutionResult，位于 GraphFlow 节点执行主流程。"""
        from scheduler.executor import NodeExecutionResult

        agent_name = str(node_payload.get("agent") or "")
        profile = self._profiles(agent_name)
        task = self._build_task(run_id, graph, state, node_payload, profile)
        result = self._runner.run(task, profile)
        artifact_path = self._write_node_artifact(run_id, node_payload, result)
        artifact_type = {
            "test": "test-result",
            "gate": "gate-result",
            "plan_gate": "gate-result",
        }.get(str(node_payload.get("type")), "node-result")
        artifacts = [{
            "type": artifact_type,
            "path": relative_to_root(self.root, artifact_path),
            "description": f"Harness TaskResult mapping for {node_payload['id']}",
        }]
        if result.status == TaskResultStatus.SUCCEEDED:
            return NodeExecutionResult("success", result.summary, artifacts)
        if result.failure_kind == "spec_gap":
            return NodeExecutionResult("spec_change", result.summary, artifacts, result.error_message)
        if result.failure_kind in {"needs_human", "needs_reconciliation"} or result.status == TaskResultStatus.CANCELLED:
            return NodeExecutionResult("l3_review", result.summary, artifacts, result.error_message)
        retry_marker = "harness_retryable" if result.retryable else "harness_non_retryable"
        failure = result.failure_kind or result.status.value
        error = f"{retry_marker}:{failure}: {result.error_message or result.summary}"
        return NodeExecutionResult("failed", result.summary, artifacts, error)

    def _build_task(
        self,
        run_id: str,
        graph: dict[str, Any],
        state: RunState,
        node_payload: dict[str, Any],
        profile: AgentProfile,
    ) -> Task:
        node_id = str(node_payload["id"])
        digest = hashlib.sha256(f"{run_id}:{node_id}".encode("utf-8")).hexdigest()[:24]
        contract = DEFAULT_TOOL_CONTRACTS.get(str(node_payload.get("agent") or ""))
        tools = self._map_tools(contract.allowed_tools if contract else [], profile.tools)
        context_ref = node_payload.get("context_pack_path")
        acceptance = node_payload.get("harness_acceptance", [])
        if not isinstance(acceptance, list) or not acceptance:
            acceptance = build_harness_acceptance(
                self.root,
                state,
                node_id,
                list(node_payload.get("acceptance_criteria", []) or []),
            )
        objective = str(
            node_payload.get("implementation_focus")
            or node_payload.get("label")
            or f"Execute GraphFlow node {node_id}"
        )
        return Task(
            task_id=f"task-{digest}",
            graph_run_id=run_id,
            node_id=node_id,
            objective=objective,
            agent_profile=profile.to_ref(),
            permissions=TaskPermissions(tools=tools),
            budget=self._budget(node_payload),
            input_refs=() if context_ref is None else (str(context_ref),),
            acceptance=tuple(dict(item) for item in acceptance if isinstance(item, dict)),
            version_set={
                "graph_schema": str(graph.get("schema_version", "unknown")),
                "spec_id": state.bound_spec.spec_id,
                "spec_version": str(state.bound_spec.version),
                "spec_hash": state.bound_spec.content_hash,
            },
        )

    def _map_tools(self, graph_tools: list[str], profile_tools: tuple[str, ...]) -> tuple[str, ...]:
        mapped: set[str] = set(profile_tools)
        for name in graph_tools:
            if name.startswith("read_"):
                mapped.add("read_file")
            elif name in {"write_json", "code_edit"}:
                mapped.add("write_file")
            elif name.startswith("shell_"):
                mapped.add("shell_exec")
        return tuple(sorted(mapped))

    def _budget(self, node_payload: dict[str, Any]) -> TaskBudget:
        payload = node_payload.get("harness_budget")
        return TaskBudget.from_dict(payload) if isinstance(payload, dict) else TaskBudget()

    def _write_node_artifact(
        self,
        run_id: str,
        node_payload: dict[str, Any],
        result: TaskResult,
    ) -> Path:
        path = self.root / ".loop" / "runs" / run_id / "artifacts" / f"{node_payload['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, {
            "run_id": run_id,
            "node_id": node_payload["id"],
            "node_type": node_payload.get("type"),
            "agent": node_payload.get("agent"),
            "status": result.status.value,
            "summary": result.summary,
            "task_id": result.task_id,
            "execution_id": result.execution_id,
            "result_ref": (
                f".loop/harness/executions/{result.execution_id}/result.json"
            ),
            "artifacts": [item.to_dict() for item in result.artifacts],
            "evidence": [item.to_dict() for item in result.evidence],
            "validation_status": result.validation.get("status"),
            "failure_kind": result.failure_kind,
            "retryable": result.retryable,
            "completed_at": utc_now(),
        })
        return path
