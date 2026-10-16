from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .agent_invoker import build_agent_context_pack
from .conformance import generate_conformance_report
from .drg import validate_execution_graph_payload
from .evidence import generate_evidence_manifest
from .events import EventLogger
from .gate_auto import run_gate_checks, serialize_check_results
from .runs import GraphRunStore, RunState, read_json, relative_to_root, utc_now, write_json
from .spec_change import create_spec_change_request, scr_artifact_ref
from .retry_policy import RetryPolicy, RetryPolicyRunner
from .state import ValidationIssue
from .tool_contracts import generate_tool_contract_report, validate_node_result_artifacts


EXECUTABLE_RUN_STATUSES = {"BOUND", "PLANNING", "RUNNING"}
MAX_SPEC_REBOUND_COUNT = 3  # V-8: circuit breaker for spec_rebound events
NodeHandler = Callable[[str, dict[str, Any], RunState, dict[str, Any]], "NodeExecutionResult"]


@dataclass(frozen=True)
class NodeExecutionResult:
    status: str
    summary: str
    artifacts: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class ExecutorResult:
    run_id: str
    status: str
    executed_nodes: list[str] = field(default_factory=list)
    blocked_nodes: list[str] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


class DRGExecutor:
    def __init__(
        self,
        root: Path,
        node_handlers: dict[str, NodeHandler] | None = None,
        agent_handlers: dict[str, NodeHandler] | None = None,
        retry_policy: RetryPolicy | None = None,
        wall_clock_timeout_seconds: float | None = None,
    ):
        self.root = root
        self.store = GraphRunStore(root)
        self.events = EventLogger(root)
        self.node_handlers = node_handlers or {}
        self.agent_handlers = agent_handlers or {}
        self.retry_policy_runner = RetryPolicyRunner(root, retry_policy)
        self.wall_clock_timeout_seconds = wall_clock_timeout_seconds

    def _check_run_health(self, run_id: str) -> ExecutorResult | None:
        """Read events.jsonl to detect runaway conditions (lightweight, no RunState mutation beyond FAILED).

        Returns an ExecutorResult with FAILED status if unhealthy, else None
        to allow normal execution.

        V-8: spec_rebound count >= MAX_SPEC_REBOUND_COUNT → circuit breaker
        V-9: cumulative wall-clock (first→last event timestamp) > timeout
        V-10 (secondary): node_started without terminal event → stuck node
        """
        events = self.events.read_run(run_id)
        if not events:
            return None

        state = self.store.read_state(run_id)

        # V-8: count spec_rebound events (written by rebind_spec)
        rebound_count = sum(1 for e in events if e.get("type") == "spec_rebound")
        if rebound_count >= MAX_SPEC_REBOUND_COUNT:
            state.status = "FAILED"
            state.last_error = (
                f"spec rebound circuit breaker: {rebound_count} spec_rebound events "
                f"(limit {MAX_SPEC_REBOUND_COUNT})"
            )
            state.current_nodes = []
            self.store.write_state(state, f"{len(state.completed_nodes):03d}-spec-rebound-limit.json")
            self.events.append_run(
                run_id,
                "run_health_check_failed",
                {"run_id": run_id, "reason": "spec_rebound_limit", "rebound_count": rebound_count},
            )
            return ExecutorResult(run_id=run_id, status="FAILED")

        # V-9: cumulative wall-clock time from first to last event.
        # Only applies when there's evidence of prior node execution (resumed
        # run), not for a fresh run() call where per-call timeout (V-7) is
        # the primary defense.
        if self.wall_clock_timeout_seconds is not None:
            has_prior_execution = any(e.get("type") == "node_started" for e in events)
            if has_prior_execution:
                try:
                    first_ts = datetime.fromisoformat(events[0]["timestamp"])
                    last_ts = datetime.fromisoformat(events[-1]["timestamp"])
                    elapsed = (last_ts - first_ts).total_seconds()
                    if elapsed > self.wall_clock_timeout_seconds:
                        state.status = "FAILED"
                        state.last_error = (
                            f"cumulative wall-clock timeout: {elapsed:.1f}s exceeds "
                            f"limit {self.wall_clock_timeout_seconds}s"
                        )
                        state.current_nodes = []
                        self.store.write_state(state, f"{len(state.completed_nodes):03d}-cumulative-timeout.json")
                        self.events.append_run(
                            run_id,
                            "run_health_check_failed",
                            {
                                "run_id": run_id,
                                "reason": "cumulative_timeout",
                                "elapsed_seconds": round(elapsed, 1),
                                "timeout_seconds": self.wall_clock_timeout_seconds,
                            },
                        )
                        return ExecutorResult(run_id=run_id, status="FAILED")
                except (KeyError, ValueError):
                    pass  # malformed event timestamp — skip this check

        # V-10 (secondary): node_started without matching terminal event.
        # "Terminal" = node_completed, node_failed, spec_change_requested,
        # or l3_review_required (properly paused nodes are not "stuck").
        started = [
            e.get("node_id") for e in events
            if e.get("type") == "node_started" and "node_id" in e
        ]
        finished = set(
            e.get("node_id") for e in events
            if e.get("type") in ("node_completed", "node_failed", "spec_change_requested", "l3_review_required")
            and "node_id" in e
        )
        stuck: list[str] = []
        for node_id in started:
            if node_id and node_id not in finished and node_id not in stuck:
                stuck.append(node_id)
        if stuck:
            state.status = "FAILED"
            state.last_error = f"stuck node(s) detected: {', '.join(stuck)}"
            state.current_nodes = []
            for node_id in stuck:
                if node_id not in state.failed_nodes:
                    state.failed_nodes.append(node_id)
            self.store.write_state(state, f"{len(state.completed_nodes):03d}-stuck-nodes.json")
            self.events.append_run(
                run_id,
                "run_health_check_failed",
                {"run_id": run_id, "reason": "stuck_nodes", "nodes": stuck},
            )
            return ExecutorResult(run_id=run_id, status="FAILED")

        return None

    def run(self, run_id: str, max_steps: int | None = None) -> ExecutorResult:
        graph = self.load_graph(run_id)
        validation = validate_execution_graph_payload(graph)
        if not validation.ok:
            return ExecutorResult(run_id=run_id, status="INVALID_GRAPH", issues=validation.issues)
        tool_contract = generate_tool_contract_report(self.root, run_id, graph)
        if tool_contract.issues:
            return ExecutorResult(run_id=run_id, status="INVALID_TOOL_CONTRACT", issues=tool_contract.issues)

        state = self.store.read_state(run_id)
        if state.status not in EXECUTABLE_RUN_STATUSES:
            issue = ValidationIssue(
                code="executor.invalid_run_status",
                severity="critical",
                message=f"cannot execute GraphRun in status: {state.status}",
            )
            return ExecutorResult(run_id=run_id, status=state.status, issues=[issue])

        # Lightweight health check via event log (V-8/V-9/V-10)
        health = self._check_run_health(run_id)
        if health is not None:
            return health

        state.status = "RUNNING"
        executed: list[str] = []
        limit = max_steps if max_steps is not None else len(graph["nodes"])
        start_time = time.monotonic()

        while len(executed) < limit:
            # Wall-clock timeout check (漏洞7)
            if self.wall_clock_timeout_seconds is not None:
                elapsed = time.monotonic() - start_time
                if elapsed > self.wall_clock_timeout_seconds:
                    state.status = "FAILED"
                    state.last_error = (
                        f"wall-clock timeout: {elapsed:.1f}s exceeds "
                        f"limit {self.wall_clock_timeout_seconds}s"
                    )
                    self.store.write_state(state, f"{len(state.completed_nodes):03d}-timeout.json")
                    self.events.append_run(
                        run_id,
                        "run_timeout",
                        {
                            "run_id": run_id,
                            "elapsed_seconds": round(elapsed, 1),
                            "timeout_seconds": self.wall_clock_timeout_seconds,
                            "executed_nodes": executed,
                        },
                    )
                    return ExecutorResult(
                        run_id=run_id,
                        status="FAILED",
                        executed_nodes=executed,
                    )
            ready = self.ready_nodes(graph, state)
            if not ready:
                break
            node_payload = ready[0]
            node_id = node_payload["id"]
            self.start_node(state, node_id)
            context_pack = build_agent_context_pack(self.root, run_id, graph, state, node_payload)
            if context_pack.issues:
                error = "; ".join(f"{issue.code}: {issue.message}" for issue in context_pack.issues)
                self.retry_policy_runner.handle_failure(state, node_payload, error)
                updated_state = self.store.read_state(run_id)
                return ExecutorResult(
                    run_id=run_id,
                    status=updated_state.status,
                    executed_nodes=executed,
                    blocked_nodes=updated_state.blocked_nodes,
                    issues=context_pack.issues,
                )
            assert context_pack.path is not None
            self.events.append_run(
                run_id,
                "agent_context_prepared",
                {"run_id": run_id, "node_id": node_id, "path": relative_to_root(self.root, context_pack.path)},
            )
            node_payload_with_context = dict(node_payload)
            node_payload_with_context["context_pack_path"] = relative_to_root(self.root, context_pack.path)
            try:
                result = self.execute_node(run_id, graph, state, node_payload_with_context)
            except Exception as exc:
                self.retry_policy_runner.handle_failure(state, node_payload_with_context, str(exc))
                updated_state = self.store.read_state(run_id)
                return ExecutorResult(
                    run_id=run_id,
                    status=updated_state.status,
                    executed_nodes=executed,
                    blocked_nodes=updated_state.blocked_nodes,
                )
            if result.status == "spec_change":
                self.await_spec_change(state, node_id, result.error or result.summary, result.artifacts)
                return ExecutorResult(run_id=run_id, status=state.status, executed_nodes=executed)
            if result.status == "l3_review":
                self.await_human_review(state, node_id, result.summary, result.error, result.artifacts)
                return ExecutorResult(run_id=run_id, status=state.status, executed_nodes=executed)
            if result.status != "success":
                self.retry_policy_runner.handle_failure(state, node_payload_with_context, result.error or result.summary)
                updated_state = self.store.read_state(run_id)
                return ExecutorResult(
                    run_id=run_id,
                    status=updated_state.status,
                    executed_nodes=executed,
                    blocked_nodes=updated_state.blocked_nodes,
                )
            artifact_validation = validate_node_result_artifacts(self.root, run_id, node_payload, result.artifacts)
            if not artifact_validation.ok:
                error = "; ".join(f"{issue.code}: {issue.message}" for issue in artifact_validation.issues)
                self.retry_policy_runner.handle_failure(state, node_payload_with_context, error)
                updated_state = self.store.read_state(run_id)
                return ExecutorResult(
                    run_id=run_id,
                    status=updated_state.status,
                    executed_nodes=executed,
                    blocked_nodes=updated_state.blocked_nodes,
                    issues=artifact_validation.issues,
                )
            self.complete_node(state, node_payload_with_context, result)
            executed.append(node_id)

            # Graph hot-reload: after drg_compile_node, the plan may have
            # been refined from design spec and the graph recompiled.
            # Reload to pick up new WP nodes.  Governance head/tail node IDs
            # are fixed, so state.completed_nodes remains valid.
            if node_id == "drg_compile_node":
                graph = self.load_graph(run_id)
                limit = len(graph["nodes"])

        remaining = self.remaining_nodes(graph, state)
        if not remaining:
            state.status = "DONE"
            state.current_nodes = []
            self.store.write_state(state, "999-done.json")
            manifest = generate_evidence_manifest(self.root, run_id, state)
            if manifest.issues:
                error = "; ".join(f"{issue.code}: {issue.message}" for issue in manifest.issues)
                state.status = "ESCALATED"
                state.last_error = error
                self.store.write_state(state, "999-evidence-escalated.json")
                self.events.append_run(run_id, "run_escalated", {"run_id": run_id, "reason": error})
                return ExecutorResult(run_id=run_id, status=state.status, executed_nodes=executed, issues=manifest.issues)
            self.events.append_run(run_id, "run_done", {"run_id": run_id})
        else:
            state.status = "RUNNING"
            state.current_nodes = [node["id"] for node in self.ready_nodes(graph, state)]
            state.blocked_nodes = [node["id"] for node in remaining if node["id"] not in state.current_nodes]
            self.store.write_state(state)

        return ExecutorResult(
            run_id=run_id,
            status=state.status,
            executed_nodes=executed,
            blocked_nodes=state.blocked_nodes,
        )

    def load_graph(self, run_id: str) -> dict[str, Any]:
        return read_json(self.store.run_dir(run_id) / "execution-graph.json")

    def ready_nodes(self, graph: dict[str, Any], state: RunState) -> list[dict[str, Any]]:
        completed = set(state.completed_nodes)
        failed = set(state.failed_nodes)
        nodes = graph["nodes"]
        incoming = incoming_edges(graph)
        return [
            node
            for node in nodes
            if node["id"] not in completed
            and node["id"] not in failed
            and all(source in completed for source in incoming.get(node["id"], []))
        ]

    def remaining_nodes(self, graph: dict[str, Any], state: RunState) -> list[dict[str, Any]]:
        completed = set(state.completed_nodes)
        failed = set(state.failed_nodes)
        return [node for node in graph["nodes"] if node["id"] not in completed and node["id"] not in failed]

    def start_node(self, state: RunState, node_id: str) -> None:
        state.current_nodes = [node_id]
        self.store.write_state(state)
        self.events.append_run(state.run_id, "node_started", {"run_id": state.run_id, "node_id": node_id})

    def execute_node(self, run_id: str, graph: dict[str, Any], state: RunState, node_payload: dict[str, Any]) -> NodeExecutionResult:
        if node_payload["id"] in self.node_handlers:
            return self.node_handlers[node_payload["id"]](run_id, graph, state, node_payload)
        agent_name = node_payload.get("agent")
        if agent_name and agent_name in self.agent_handlers:
            return self.agent_handlers[agent_name](run_id, graph, state, node_payload)
        node_id = node_payload.get("id")
        if node_id == "done_node":
            return self.execute_done_node(run_id, node_payload)
        if node_id == "design_spec_node":
            return self.execute_design_spec_node(run_id, state, node_payload)
        if node_id == "design_gate_node":
            return self.execute_design_gate_node(run_id, state, node_payload)
        if node_id == "drg_compile_node":
            return self.execute_drg_compile_node(run_id, state, node_payload)
        node_type = node_payload["type"]
        if node_type in {"gate", "plan_gate"}:
            return self.execute_gate_node(run_id, state, node_payload)
        if node_type == "test":
            return self.execute_test_node(run_id, state, node_payload)
        if node_type == "review":
            return self.execute_review_node(run_id, node_payload)
        if node_type == "deploy":
            return self.execute_deploy_node(run_id, node_payload)
        if node_type == "conformance":
            return self.execute_conformance_node(run_id, graph, state, node_payload)
        artifact_path = self.write_node_result(run_id, node_payload)
        return NodeExecutionResult(
            status="success",
            summary=f"{node_payload['id']} completed by deterministic executor",
            artifacts=[
                {
                    "type": "node-result",
                    "path": relative_to_root(self.root, artifact_path),
                    "description": f"Execution result for {node_payload['id']}",
                }
            ],
        )

    def execute_gate_node(self, run_id: str, state: RunState, node_payload: dict[str, Any]) -> NodeExecutionResult:
        checks = run_gate_checks(self.root, state, node_payload["id"])
        verdict = "PASS" if all(check.passed for check in checks) else "FAIL"
        path = self.store.run_dir(run_id) / "artifacts" / f"{node_payload['id']}.json"
        write_json(
            path,
            {
                "run_id": run_id,
                "node_id": node_payload["id"],
                "node_type": node_payload["type"],
                "agent": node_payload.get("agent"),
                "status": "success" if verdict == "PASS" else "failed",
                "verdict": verdict,
                "checks": serialize_check_results(checks),
                "completed_at": utc_now(),
                "summary": f"{node_payload['id']} {'passed' if verdict == 'PASS' else 'failed'} gate checks",
            },
        )
        if verdict != "PASS":
            return NodeExecutionResult(
                status="failed",
                summary=f"{node_payload['id']} failed gate checks",
                error=f"{node_payload['id']} failed gate checks",
            )
        return self.artifact_result(node_payload["id"], "gate-result", path, f"Gate result for {node_payload['id']}")

    def execute_test_node(self, run_id: str, state: RunState, node_payload: dict[str, Any]) -> NodeExecutionResult:
        checks = run_gate_checks(
            self.root,
            state,
            node_payload["id"],
            acceptance_ids=list(node_payload.get("acceptance_criteria", []) or []),
        )
        verdict = "PASS" if all(check.passed for check in checks) else "FAIL"
        path = self.store.run_dir(run_id) / "artifacts" / f"{node_payload['id']}.json"
        write_json(
            path,
            {
                "run_id": run_id,
                "node_id": node_payload["id"],
                "node_type": node_payload["type"],
                "agent": node_payload.get("agent"),
                "status": "success" if verdict == "PASS" else "failed",
                "verdict": verdict,
                "checks": serialize_check_results(checks),
                "acceptance_criteria": list(node_payload.get("acceptance_criteria", []) or []),
                "completed_at": utc_now(),
                "summary": f"{node_payload['id']} {'passed' if verdict == 'PASS' else 'failed'} test checks",
            },
        )
        if verdict != "PASS":
            return NodeExecutionResult(
                status="failed",
                summary=f"{node_payload['id']} failed test checks",
                error=f"{node_payload['id']} failed test checks",
            )
        return self.artifact_result(node_payload["id"], "test-result", path, f"Test result for {node_payload['id']}")

    def execute_review_node(self, run_id: str, node_payload: dict[str, Any]) -> NodeExecutionResult:
        """Deterministic fallback for delivery_review_node.

        Always returns APPROVE.  A real review agent (plugged in via
        ``agent_handlers["review-agent"]``) can return ``status="l3_review"``
        to trigger the ``AWAITING_HUMAN`` pause path (see
        ``await_human_review`` / ``resume_human_review``), or
        ``status="failed"`` to trigger retry / escalation.
        """
        path = self.store.run_dir(run_id) / "artifacts" / f"{node_payload['id']}.json"
        write_json(
            path,
            {
                "run_id": run_id,
                "node_id": node_payload["id"],
                "node_type": node_payload["type"],
                "agent": node_payload.get("agent"),
                "status": "success",
                "verdict": "APPROVE",
                "issues": [],
                "completed_at": utc_now(),
                "summary": f"{node_payload['id']} approved by deterministic review handler",
            },
        )
        return self.artifact_result(node_payload["id"], "review-result", path, f"Review result for {node_payload['id']}")

    def execute_deploy_node(self, run_id: str, node_payload: dict[str, Any]) -> NodeExecutionResult:
        path = self.store.run_dir(run_id) / "artifacts" / f"{node_payload['id']}.json"
        write_json(
            path,
            {
                "run_id": run_id,
                "node_id": node_payload["id"],
                "node_type": node_payload["type"],
                "agent": node_payload.get("agent"),
                "status": "success",
                "deploy": {"success": True, "environment": "local-deterministic"},
                "completed_at": utc_now(),
                "summary": f"{node_payload['id']} produced deterministic deploy evidence",
            },
        )
        return self.artifact_result(node_payload["id"], "deploy-result", path, f"Deploy result for {node_payload['id']}")

    def execute_conformance_node(
        self,
        run_id: str,
        graph: dict[str, Any],
        state: RunState,
        node_payload: dict[str, Any],
    ) -> NodeExecutionResult:
        report = generate_conformance_report(self.root, run_id, graph, state.completed_nodes)
        if report.issues:
            error = "; ".join(f"{issue.code}: {issue.message}" for issue in report.issues)
            return NodeExecutionResult(status="failed", summary="conformance report generation failed", error=error)
        status = str(report.payload["overall"])
        if status == "UNVERIFIED":
            # Evidence is missing or unverifiable — the spec-to-evidence mapping
            # has a gap, so raise a SpecChangeRequest for the spec owner.
            scr = create_spec_change_request(
                self.root,
                state,
                raised_by=node_payload.get("agent") or node_payload["id"],
                request_type="spec_gap",
                reason=f"conformance {status}: required evidence is missing or unverifiable",
                affected_nodes=[node_payload["id"]],
                evidence_refs=[relative_to_root(self.root, report.path)] if report.path else [],
                conformance_report=report.payload,
            )
            if scr.issues:
                error = "; ".join(f"{issue.code}: {issue.message}" for issue in scr.issues)
                return NodeExecutionResult(status="failed", summary="SpecChangeRequest generation failed", error=error)
            assert scr.path is not None
            return NodeExecutionResult(
                status="spec_change",
                summary=f"conformance {status}; SpecChangeRequest raised",
                artifacts=[
                    {
                        "type": "conformance-report",
                        "path": relative_to_root(self.root, report.path),
                        "description": "Conformance report",
                    },
                    scr_artifact_ref(self.root, scr.path),
                ],
                error=f"conformance {status}",
            )
        if status == "FAIL":
            # Spec defect: requirement has no acceptance coverage, or automated
            # acceptance has empty check/expect. This is an acceptance_bug, not
            # a spec_gap — the spec itself is defective and must be revised.
            scr = create_spec_change_request(
                self.root,
                state,
                raised_by=node_payload.get("agent") or node_payload["id"],
                request_type="acceptance_bug",
                reason=f"conformance {status}: spec defect detected (orphan requirement or empty automated acceptance)",
                affected_nodes=[node_payload["id"]],
                evidence_refs=[relative_to_root(self.root, report.path)] if report.path else [],
                conformance_report=report.payload,
            )
            if scr.issues:
                error = "; ".join(f"{issue.code}: {issue.message}" for issue in scr.issues)
                return NodeExecutionResult(status="failed", summary="SpecChangeRequest generation failed", error=error)
            assert scr.path is not None
            return NodeExecutionResult(
                status="spec_change",
                summary=f"conformance {status}; SpecChangeRequest raised (acceptance_bug)",
                artifacts=[
                    {
                        "type": "conformance-report",
                        "path": relative_to_root(self.root, report.path),
                        "description": "Conformance report",
                    },
                    scr_artifact_ref(self.root, scr.path),
                ],
                error=f"conformance {status}",
            )
        # PASS or GAP_FOUND: proceed to delivery review. A GAP_FOUND here means
        # the deterministic layer found a citable, structural gap (e.g. a core
        # intent document is missing) — it is recorded in the conformance
        # report's ``intent_audit`` section and handed to the review agent,
        # which performs the semantic intended-vs-implemented comparison and
        # decides REJECT (-> RETRY) or APPROVE. Intent-implementation gaps are
        # an implementation/documentation problem, not a spec gap, so they do
        # not raise a SpecChangeRequest.
        assert report.path is not None
        return self.artifact_result(node_payload["id"], "conformance-report", report.path, "Conformance report")

    def execute_design_spec_node(
        self,
        run_id: str,
        state: RunState,
        node_payload: dict[str, Any],
    ) -> NodeExecutionResult:
        from .design_spec import generate_minimal_design_spec, write_design_spec

        spec = generate_minimal_design_spec(self.root, run_id)
        spec_path = write_design_spec(self.root, run_id, spec.payload)
        artifact_path = self.store.run_dir(run_id) / "artifacts" / f"{node_payload['id']}.json"
        write_json(
            artifact_path,
            {
                "run_id": run_id,
                "node_id": node_payload["id"],
                "node_type": node_payload["type"],
                "agent": node_payload.get("agent"),
                "status": "success",
                "design_spec_path": relative_to_root(self.root, spec_path),
                "completed_at": utc_now(),
                "summary": f"{node_payload['id']} produced design-spec.json",
            },
        )
        return NodeExecutionResult(
            status="success",
            summary=f"{node_payload['id']} produced design-spec.json",
            artifacts=[
                {
                    "type": "node-result",
                    "path": relative_to_root(self.root, artifact_path),
                    "description": f"Design spec result for {node_payload['id']}",
                }
            ],
        )

    def execute_design_gate_node(
        self,
        run_id: str,
        state: RunState,
        node_payload: dict[str, Any],
    ) -> NodeExecutionResult:
        from .design_spec import run_design_gate_checks, serialize_design_gate_checks

        checks = run_design_gate_checks(self.root, run_id)
        verdict = "PASS" if all(check.passed for check in checks) else "FAIL"
        path = self.store.run_dir(run_id) / "artifacts" / f"{node_payload['id']}.json"
        write_json(
            path,
            {
                "run_id": run_id,
                "node_id": node_payload["id"],
                "node_type": node_payload["type"],
                "agent": node_payload.get("agent"),
                "status": "success" if verdict == "PASS" else "failed",
                "verdict": verdict,
                "checks": serialize_design_gate_checks(checks),
                "completed_at": utc_now(),
                "summary": f"{node_payload['id']} {'passed' if verdict == 'PASS' else 'failed'} design gate checks",
            },
        )
        if verdict != "PASS":
            return NodeExecutionResult(
                status="failed",
                summary=f"{node_payload['id']} failed design gate checks",
                error=f"{node_payload['id']} failed design gate checks",
            )
        return self.artifact_result(node_payload["id"], "gate-result", path, f"Design gate result for {node_payload['id']}")

    def execute_drg_compile_node(
        self,
        run_id: str,
        state: RunState,
        node_payload: dict[str, Any],
    ) -> NodeExecutionResult:
        """DRG compile node — P1: refine plan from design spec + recompile graph.

        When the design spec is "rich" (has api_contracts, data_model, or
        extra component_boundaries), the execution plan is refined and the
        graph is recompiled.  The ``run()`` loop hot-reloads the graph
        after this node completes.

        When the design spec is minimal (deterministic fallback), no
        refinement occurs — the existing graph stays unchanged, preserving
        backward compatibility with stub-based tests.
        """
        from .plans import refine_plan_from_design_spec

        refined, plan = refine_plan_from_design_spec(self.root, run_id)

        if refined:
            from .drg import compile_execution_graph

            graph_result = compile_execution_graph(self.root, run_id)
            if graph_result.issues:
                # Compilation failed — old graph stays on disk.
                # Treat as not refined; executor continues with old graph.
                refined = False

        artifact_path = self.store.run_dir(run_id) / "artifacts" / f"{node_payload['id']}.json"
        write_json(
            artifact_path,
            {
                "run_id": run_id,
                "node_id": node_payload["id"],
                "node_type": node_payload["type"],
                "agent": node_payload.get("agent"),
                "status": "success",
                "plan_refined": refined,
                "wp_count": len(plan.work_packages) if refined else None,
                "completed_at": utc_now(),
                "summary": (
                    f"{node_payload['id']} refined plan from design spec "
                    f"({len(plan.work_packages)} WPs)"
                    if refined
                    else f"{node_payload['id']} completed (no refinement — minimal design spec)"
                ),
            },
        )
        return NodeExecutionResult(
            status="success",
            summary=f"{node_payload['id']} completed",
            artifacts=[
                {
                    "type": "node-result",
                    "path": relative_to_root(self.root, artifact_path),
                    "description": f"DRG compile result for {node_payload['id']}",
                }
            ],
        )

    def execute_done_node(self, run_id: str, node_payload: dict[str, Any]) -> NodeExecutionResult:
        """done_node is a terminal marker node.

        Writes a minimal artifact (with ``terminal_marker: True``) for
        tool-contract compliance.  The actual DONE state transition
        (``state.status = "DONE"``, evidence manifest generation, event
        logging) is handled by the post-loop check in ``run()``, NOT here.
        This resolves the "double definition" issue (漏洞6): the node
        handler only records that the terminal marker was reached, while
        the post-loop check is the sole authority for the DONE transition.
        """
        path = self.store.run_dir(run_id) / "artifacts" / f"{node_payload['id']}.json"
        write_json(
            path,
            {
                "run_id": run_id,
                "node_id": node_payload["id"],
                "node_type": node_payload["type"],
                "agent": node_payload.get("agent"),
                "status": "success",
                "terminal_marker": True,
                "completed_at": utc_now(),
                "summary": f"{node_payload['id']} terminal marker reached",
            },
        )
        return NodeExecutionResult(
            status="success",
            summary=f"{node_payload['id']} terminal marker reached",
            artifacts=[
                {
                    "type": "node-result",
                    "path": relative_to_root(self.root, path),
                    "description": f"Terminal marker for {node_payload['id']}",
                }
            ],
        )

    def artifact_result(self, node_id: str, artifact_type: str, path: Path, description: str) -> NodeExecutionResult:
        return NodeExecutionResult(
            status="success",
            summary=f"{node_id} completed",
            artifacts=[
                {
                    "type": artifact_type,
                    "path": relative_to_root(self.root, path),
                    "description": description,
                }
            ],
        )

    def write_node_result(self, run_id: str, node_payload: dict[str, Any]) -> Path:
        path = self.store.run_dir(run_id) / "artifacts" / f"{node_payload['id']}.json"
        write_json(
            path,
            {
                "run_id": run_id,
                "node_id": node_payload["id"],
                "node_type": node_payload["type"],
                "agent": node_payload.get("agent"),
                "status": "success",
                "completed_at": utc_now(),
                "summary": f"{node_payload['id']} completed by deterministic executor",
            },
        )
        return path

    def complete_node(self, state: RunState, node_payload: dict[str, Any], result: NodeExecutionResult) -> None:
        node_id = node_payload["id"]
        if node_id not in state.completed_nodes:
            state.completed_nodes.append(node_id)
        state.current_nodes = []
        for artifact in result.artifacts:
            self.store.append_artifact_ref(
                state,
                artifact["type"],
                self.root / artifact["path"],
                artifact["description"],
            )
            self.events.append_run(
                state.run_id,
                "artifact_written",
                {"run_id": state.run_id, "node_id": node_id, "path": artifact["path"], "artifact_type": artifact["type"]},
            )
        self.store.write_state(state, f"{len(state.completed_nodes):03d}-{node_id}.json")
        self.events.append_run(
            state.run_id,
            "node_completed",
            {"run_id": state.run_id, "node_id": node_id, "summary": result.summary},
        )

    def fail_node(self, state: RunState, node_id: str, error: str) -> None:
        if node_id not in state.failed_nodes:
            state.failed_nodes.append(node_id)
        state.current_nodes = []
        state.status = "FAILED"
        state.last_error = error
        self.store.write_state(state, f"{len(state.completed_nodes):03d}-{node_id}-failed.json")
        self.events.append_run(state.run_id, "node_failed", {"run_id": state.run_id, "node_id": node_id, "error": error})

    def await_spec_change(self, state: RunState, node_id: str, reason: str, artifacts: list[dict[str, str]]) -> None:
        state.current_nodes = []
        state.blocked_nodes = [node_id]
        state.status = "AWAITING_SPEC_CHANGE"
        state.last_error = reason
        for artifact in artifacts:
            self.store.append_artifact_ref(
                state,
                artifact["type"],
                self.root / artifact["path"],
                artifact["description"],
            )
            self.events.append_run(
                state.run_id,
                "artifact_written",
                {"run_id": state.run_id, "node_id": node_id, "path": artifact["path"], "artifact_type": artifact["type"]},
            )
        self.store.write_state(state, f"{len(state.completed_nodes):03d}-{node_id}-awaiting-spec-change.json")
        self.events.append_run(
            state.run_id,
            "spec_change_requested",
            {"run_id": state.run_id, "node_id": node_id, "reason": reason},
        )

    def await_human_review(
        self,
        state: RunState,
        node_id: str,
        summary: str,
        reason: str | None,
        artifacts: list[dict[str, str]],
    ) -> None:
        """Pause the run for L3 human review.

        Triggered when a review handler returns ``status="l3_review"``.
        The run enters ``AWAITING_HUMAN`` and stays there until
        ``resume_human_review`` is called with APPROVE or REJECT.
        """
        state.current_nodes = []
        state.blocked_nodes = [node_id]
        state.status = "AWAITING_HUMAN"
        state.last_error = reason or summary
        for artifact in artifacts:
            self.store.append_artifact_ref(
                state,
                artifact["type"],
                self.root / artifact["path"],
                artifact["description"],
            )
            self.events.append_run(
                state.run_id,
                "artifact_written",
                {"run_id": state.run_id, "node_id": node_id, "path": artifact["path"], "artifact_type": artifact["type"]},
            )
        self.store.write_state(state, f"{len(state.completed_nodes):03d}-{node_id}-awaiting-human.json")
        self.events.append_run(
            state.run_id,
            "l3_review_required",
            {"run_id": state.run_id, "node_id": node_id, "reason": reason or summary},
        )

    def resume_spec_change(self, run_id: str, new_snapshot_path: Path) -> ExecutorResult:
        """Resume a run from ``AWAITING_SPEC_CHANGE``.

        Rebinds the run to a new SpecSnapshot version (published via the
        SCR → revision draft → approve → publish flow), resets all completed
        / failed / blocked nodes, regenerates the execution plan and graph,
        then continues execution from ``spec_binding_node``.
        """
        state = self.store.read_state(run_id)
        if state.status != "AWAITING_SPEC_CHANGE":
            issue = ValidationIssue(
                code="executor.resume_spec_change.invalid_status",
                severity="critical",
                message=f"cannot resume: run status is {state.status}, expected AWAITING_SPEC_CHANGE",
            )
            return ExecutorResult(run_id=run_id, status=state.status, issues=[issue])

        # V-8: circuit breaker — check spec_rebound count before rebind
        health = self._check_run_health(run_id)
        if health is not None:
            return health

        state = self.store.rebind_spec(run_id, new_snapshot_path)

        state.completed_nodes = []
        state.failed_nodes = []
        state.blocked_nodes = []
        state.current_nodes = ["spec_binding_node"]
        state.retry_count = {}
        state.last_error = None
        state.status = "BOUND"
        self.store.write_state(state, "001-rebound.json")
        self.events.append_run(run_id, "run_resumed_spec_change", {"run_id": run_id})

        from .plans import generate_execution_plan
        from .drg import compile_execution_graph

        plan_result = generate_execution_plan(self.root, run_id)
        if plan_result.issues:
            state.status = "ESCALATED"
            state.last_error = "plan regeneration failed after spec rebind"
            self.store.write_state(state, "999-resume-failed.json")
            return ExecutorResult(run_id=run_id, status="ESCALATED", issues=plan_result.issues)

        graph_result = compile_execution_graph(self.root, run_id)
        if graph_result.issues:
            state.status = "ESCALATED"
            state.last_error = "graph compilation failed after spec rebind"
            self.store.write_state(state, "999-resume-failed.json")
            return ExecutorResult(run_id=run_id, status="ESCALATED", issues=graph_result.issues)

        return self.run(run_id)

    def resume_human_review(self, run_id: str, decision: str, reason: str = "") -> ExecutorResult:
        """Resume a run from ``AWAITING_HUMAN`` after L3 human review.

        ``decision`` is ``"APPROVE"`` (mark the paused node as completed and
        continue execution) or ``"REJECT"`` (escalate the run).
        """
        state = self.store.read_state(run_id)
        if state.status != "AWAITING_HUMAN":
            issue = ValidationIssue(
                code="executor.resume_human_review.invalid_status",
                severity="critical",
                message=f"cannot resume: run status is {state.status}, expected AWAITING_HUMAN",
            )
            return ExecutorResult(run_id=run_id, status=state.status, issues=[issue])

        blocked_node = state.blocked_nodes[0] if state.blocked_nodes else "delivery_review_node"

        if decision == "APPROVE":
            if blocked_node not in state.completed_nodes:
                state.completed_nodes.append(blocked_node)
            state.blocked_nodes = []
            state.current_nodes = []
            state.last_error = None
            state.status = "RUNNING"
            self.store.write_state(state, f"{len(state.completed_nodes):03d}-{blocked_node}-human-approved.json")
            self.events.append_run(
                run_id,
                "l3_review_approved",
                {"run_id": run_id, "node_id": blocked_node, "reason": reason},
            )
            return self.run(run_id)

        if decision == "REJECT":
            state.status = "ESCALATED"
            state.last_error = f"L3 human review rejected: {reason}"
            state.blocked_nodes = []
            if blocked_node not in state.failed_nodes:
                state.failed_nodes.append(blocked_node)
            self.store.write_state(state, f"{len(state.completed_nodes):03d}-{blocked_node}-human-rejected.json")
            self.events.append_run(
                run_id,
                "l3_review_rejected",
                {"run_id": run_id, "node_id": blocked_node, "reason": reason},
            )
            return ExecutorResult(run_id=run_id, status="ESCALATED", executed_nodes=state.completed_nodes)

        issue = ValidationIssue(
            code="executor.resume_human_review.invalid_decision",
            severity="critical",
            message=f"invalid decision: {decision}, expected APPROVE or REJECT",
        )
        return ExecutorResult(run_id=run_id, status=state.status, issues=[issue])


def incoming_edges(graph: dict[str, Any]) -> dict[str, list[str]]:
    incoming = {node["id"]: [] for node in graph["nodes"]}
    for edge in graph["edges"]:
        incoming.setdefault(edge["target"], []).append(edge["source"])
    return incoming
