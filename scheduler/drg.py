from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import EventLogger
from .plans import ExecutionPlan, load_execution_plan
from .runs import GraphRunStore, write_json
from .state import ValidationResult


REGISTERED_NODE_TYPES = {
    "governance",
    "plan_gate",
    "architecture",
    "compiler",
    "implementation",
    "test",
    "gate",
    "review",
    "deploy",
    "conformance",
}

MANDATORY_GOVERNANCE_NODES = [
    "spec_binding_node",
    "planning_node",
    "plan_gate_node",
    "architecture_impact_node",
    "architecture_gate_node",
    "design_spec_node",
    "design_gate_node",
    "drg_compile_node",
    "implementation_drg_node",
    "final_gate_node",
    "conformance_node",
    "delivery_review_node",
    "deploy_node",
    "done_node",
]


@dataclass(frozen=True)
class ExecutionGraph:
    path: Path | None
    payload: dict[str, Any]
    issues: list = field(default_factory=list)


def compile_execution_graph(root: Path, run_id: str) -> ExecutionGraph:
    store = GraphRunStore(root)
    plan_path = store.run_dir(run_id) / "execution-plan.json"
    plan = load_execution_plan(plan_path)
    if plan.issues:
        return ExecutionGraph(path=None, payload={}, issues=plan.issues)

    graph = build_execution_graph(plan)
    result = validate_execution_graph_payload(graph)
    if not result.ok:
        return ExecutionGraph(path=None, payload=graph, issues=result.issues)

    graph["content_hash"] = calculate_graph_hash(graph)
    path = store.run_dir(run_id) / "execution-graph.json"
    write_json(path, graph)
    EventLogger(root).append_run(
        run_id,
        "drg_compiled",
        {"run_id": run_id, "template_id": plan.template_id, "content_hash": graph["content_hash"]},
    )
    return ExecutionGraph(path=path, payload=graph, issues=[])


def build_execution_graph(plan: ExecutionPlan) -> dict[str, Any]:
    nodes = [
        node("spec_binding_node", "governance", "Spec Binding", "gate-auto"),
        node("planning_node", "governance", "Planning", "orchestrator-llm"),
        node("plan_gate_node", "plan_gate", "Plan Gate", "gate-auto"),
        node("architecture_impact_node", "architecture", "Architecture Impact", "architect-agent"),
        node("architecture_gate_node", "gate", "Architecture Gate", "gate-auto"),
        node("design_spec_node", "architecture", "Design Spec", "architect-agent"),
        node("design_gate_node", "gate", "Design Gate", "gate-auto"),
        node("drg_compile_node", "compiler", "DRG Compile", "gate-auto"),
        node("implementation_drg_node", "governance", "Implementation DRG", "orchestrator-llm"),
    ]
    edges = [
        edge("spec_binding_node", "planning_node"),
        edge("planning_node", "plan_gate_node"),
        edge("plan_gate_node", "architecture_impact_node"),
        edge("architecture_impact_node", "architecture_gate_node"),
        edge("architecture_gate_node", "design_spec_node"),
        edge("design_spec_node", "design_gate_node"),
        edge("design_gate_node", "drg_compile_node"),
        edge("drg_compile_node", "implementation_drg_node"),
    ]

    previous = "implementation_drg_node"
    work_package_ids = []
    for work_package in plan.work_packages:
        implement_id = f"{work_package['id']}_implement"
        test_id = f"{work_package['id']}_test"
        work_package_ids.extend([implement_id, test_id])
        nodes.append(
            {
                "id": implement_id,
                "type": work_package.get("node_type", "implementation"),
                "label": work_package.get("title", implement_id),
                "agent": work_package.get("agent"),
                "work_package_id": work_package.get("id"),
                "requirements": list(work_package.get("requirements", []) or []),
                "acceptance_criteria": [],
            }
        )
        nodes.append(
            {
                "id": test_id,
                "type": "test",
                "label": f"Verify {work_package.get('id')}",
                "agent": "test-agent",
                "work_package_id": work_package.get("id"),
                "requirements": [],
                "acceptance_criteria": list(work_package.get("acceptance_criteria", []) or []),
            }
        )
        edges.append(edge(previous, implement_id))
        edges.append(edge(implement_id, test_id))
        previous = test_id

    nodes.extend(
        [
            node("final_gate_node", "gate", "Final Gate", "gate-auto"),
            node("conformance_node", "conformance", "Conformance", "gate-auto"),
            node("delivery_review_node", "review", "Delivery Review", "review-agent"),
            node("deploy_node", "deploy", "Deploy", "devops-agent"),
            node("done_node", "governance", "Done", "gate-auto"),
        ]
    )
    edges.extend(
        [
            edge(previous, "final_gate_node"),
            edge("final_gate_node", "conformance_node"),
            edge("conformance_node", "delivery_review_node"),
            edge("delivery_review_node", "deploy_node"),
            edge("deploy_node", "done_node"),
        ]
    )

    for dependency in plan.payload.get("depends_on", []) or []:
        source = f"{dependency['source']}_test"
        target = f"{dependency['target']}_implement"
        if source in work_package_ids and target in work_package_ids:
            edges.append(edge(source, target))

    return {
        "schema_version": "1.0",
        "run_id": plan.payload.get("run_id"),
        "spec_id": plan.payload.get("spec_id"),
        "spec_version": plan.payload.get("spec_version"),
        "template_id": plan.template_id,
        "content_hash": "",
        "nodes": nodes,
        "edges": edges,
        "requirement_coverage": plan.payload.get("requirement_coverage", []),
        "acceptance_coverage": plan.payload.get("acceptance_coverage", []),
    }


def validate_execution_graph_payload(payload: dict[str, Any]) -> ValidationResult:
    result = ValidationResult(ok=True)
    nodes = payload.get("nodes", []) or []
    edges = payload.get("edges", []) or []
    node_ids = [item.get("id") for item in nodes if isinstance(item, dict)]
    node_id_set = set(node_ids)

    if len(node_ids) != len(node_id_set):
        result.add("execution_graph.duplicate_node", "critical", "execution graph contains duplicate node ids")

    missing_governance = sorted(set(MANDATORY_GOVERNANCE_NODES) - node_id_set)
    if missing_governance:
        result.add("execution_graph.missing_governance_node", "critical", f"missing governance nodes: {missing_governance}")

    for item in nodes:
        if not isinstance(item, dict):
            result.add("execution_graph.invalid_node", "error", "node must be an object")
            continue
        if item.get("type") not in REGISTERED_NODE_TYPES:
            result.add("execution_graph.unregistered_node_type", "critical", f"unregistered node type: {item.get('type')}")

    for item in edges:
        source = item.get("source")
        target = item.get("target")
        if source not in node_id_set or target not in node_id_set:
            result.add("execution_graph.invalid_edge", "critical", f"edge references unknown node: {source} -> {target}")

    if has_cycle(node_id_set, edges):
        result.add("execution_graph.cycle", "critical", "execution graph must be a DAG")

    implemented_requirements = set()
    verified_acceptance = set()
    for item in nodes:
        if isinstance(item, dict):
            implemented_requirements.update(item.get("requirements", []) or [])
            verified_acceptance.update(item.get("acceptance_criteria", []) or [])

    required_coverage = {item.get("requirement_id") for item in payload.get("requirement_coverage", []) or []}
    acceptance_coverage = {item.get("acceptance_id") for item in payload.get("acceptance_coverage", []) or []}
    missing_requirements = sorted(required_coverage - implemented_requirements)
    missing_acceptance = sorted(acceptance_coverage - verified_acceptance)
    if missing_requirements:
        result.add("execution_graph.missing_requirement_node", "critical", f"requirements lack implementation nodes: {missing_requirements}")
    if missing_acceptance:
        result.add("execution_graph.missing_acceptance_node", "critical", f"acceptance criteria lack verification nodes: {missing_acceptance}")

    actual_hash = payload.get("content_hash")
    if actual_hash:
        expected_hash = calculate_graph_hash(payload)
        if actual_hash != expected_hash:
            result.add("execution_graph.hash_mismatch", "critical", f"content_hash mismatch: expected {expected_hash}, got {actual_hash}")

    return result


def node(node_id: str, node_type: str, label: str, agent: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "label": label,
        "agent": agent,
        "requirements": [],
        "acceptance_criteria": [],
    }


def edge(source: str, target: str) -> dict[str, str]:
    return {"source": source, "target": target}


def has_cycle(node_ids: set[str], edges: list[dict[str, Any]]) -> bool:
    adjacency = {node_id: [] for node_id in node_ids}
    for item in edges:
        source = item.get("source")
        target = item.get("target")
        if source in adjacency and target in node_ids:
            adjacency[source].append(target)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for target in adjacency[node_id]:
            if visit(target):
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in node_ids)


def calculate_graph_hash(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("content_hash", None)
    content = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()
