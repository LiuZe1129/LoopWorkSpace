from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .events import EventLogger
from .runs import GraphRunStore, read_json, write_json
from .state import ValidationIssue, ValidationResult


@dataclass(frozen=True)
class ToolContract:
    agent: str
    node_types: set[str]
    allowed_tools: list[str]
    read_paths: list[str]
    write_paths: list[str]
    artifact_types: set[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "node_types": sorted(self.node_types),
            "allowed_tools": self.allowed_tools,
            "read_paths": self.read_paths,
            "write_paths": self.write_paths,
            "artifact_types": sorted(self.artifact_types),
        }


@dataclass(frozen=True)
class ToolContractReport:
    path: Path | None
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)


NODE_TYPE_ARTIFACT_TYPES = {
    "governance": {"node-result"},
    "plan_gate": {"gate-result"},
    "architecture": {"node-result"},
    "compiler": {"node-result"},
    "implementation": {"node-result"},
    "test": {"test-result"},
    "gate": {"gate-result"},
    "review": {"review-result"},
    "deploy": {"deploy-result"},
    "conformance": {"conformance-report"},
}


DEFAULT_TOOL_CONTRACTS: dict[str, ToolContract] = {
    "gate-auto": ToolContract(
        agent="gate-auto",
        node_types={"governance", "plan_gate", "compiler", "gate", "conformance"},
        allowed_tools=["read_json", "write_json", "shell_check", "artifact_register"],
        read_paths=["specs/**", ".loop/runs/{run_id}/**", ".loop/memory/**"],
        write_paths=[".loop/runs/{run_id}/artifacts/**", ".loop/runs/{run_id}/*.json"],
        artifact_types={"node-result", "gate-result", "conformance-report"},
    ),
    "orchestrator-llm": ToolContract(
        agent="orchestrator-llm",
        node_types={"governance"},
        allowed_tools=["read_json", "write_json", "llm_plan"],
        read_paths=["specs/**", ".loop/runs/{run_id}/**", ".loop/context.md", ".loop/memory/**"],
        write_paths=[".loop/runs/{run_id}/artifacts/**"],
        artifact_types={"node-result"},
    ),
    "architect-agent": ToolContract(
        agent="architect-agent",
        node_types={"architecture"},
        allowed_tools=["read_json", "write_json", "llm_architecture"],
        read_paths=["specs/**", ".loop/runs/{run_id}/**", "shared/**", "doc/**", ".loop/memory/**"],
        write_paths=[".loop/runs/{run_id}/artifacts/**", "shared/**"],
        artifact_types={"node-result"},
    ),
    "backend-dev": ToolContract(
        agent="backend-dev",
        node_types={"implementation"},
        allowed_tools=["read_json", "code_edit", "shell_test", "write_json"],
        read_paths=["specs/**", ".loop/runs/{run_id}/**", "projects/**", "shared/**", ".loop/memory/**"],
        write_paths=["projects/**", "shared/**", ".loop/runs/{run_id}/artifacts/**"],
        artifact_types={"node-result"},
    ),
    "frontend-dev": ToolContract(
        agent="frontend-dev",
        node_types={"implementation"},
        allowed_tools=["read_json", "code_edit", "shell_test", "write_json"],
        read_paths=["specs/**", ".loop/runs/{run_id}/**", "projects/**", "shared/**", ".loop/memory/**"],
        write_paths=["projects/**", "shared/**", ".loop/runs/{run_id}/artifacts/**"],
        artifact_types={"node-result"},
    ),
    "client-dev": ToolContract(
        agent="client-dev",
        node_types={"implementation"},
        allowed_tools=["read_json", "code_edit", "shell_test", "write_json"],
        read_paths=["specs/**", ".loop/runs/{run_id}/**", "projects/**", "shared/**", ".loop/memory/**"],
        write_paths=["projects/**", "shared/**", ".loop/runs/{run_id}/artifacts/**"],
        artifact_types={"node-result"},
    ),
    "test-agent": ToolContract(
        agent="test-agent",
        node_types={"test"},
        allowed_tools=["read_json", "shell_test", "write_json"],
        read_paths=["specs/**", ".loop/runs/{run_id}/**", "projects/**", "shared/**"],
        write_paths=[".loop/runs/{run_id}/artifacts/**"],
        artifact_types={"test-result"},
    ),
    "review-agent": ToolContract(
        agent="review-agent",
        node_types={"review"},
        allowed_tools=["read_json", "llm_review", "write_json"],
        read_paths=["specs/**", ".loop/runs/{run_id}/**", "projects/**", "shared/**", ".loop/memory/**"],
        write_paths=[".loop/runs/{run_id}/artifacts/**"],
        artifact_types={"review-result"},
    ),
    "devops-agent": ToolContract(
        agent="devops-agent",
        node_types={"deploy"},
        allowed_tools=["read_json", "shell_deploy", "write_json"],
        read_paths=["specs/**", ".loop/runs/{run_id}/**", "projects/**", "shared/**"],
        write_paths=[".loop/runs/{run_id}/artifacts/**"],
        artifact_types={"deploy-result"},
    ),
}


def generate_tool_contract_report(root: Path, run_id: str, graph: dict[str, Any] | None = None) -> ToolContractReport:
    store = GraphRunStore(root)
    graph_payload = graph or read_json(store.run_dir(run_id) / "execution-graph.json")
    validation = validate_tool_contracts(graph_payload, run_id)
    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "nodes": node_contract_entries(graph_payload, run_id),
        "issues": [issue_to_dict(issue) for issue in validation.issues],
        "overall": "PASS" if validation.ok else "FAIL",
    }
    path = store.run_dir(run_id) / "tool-contract-report.json"
    write_json(path, payload)
    EventLogger(root).append_run(
        run_id,
        "tool_contract_validated",
        {"run_id": run_id, "overall": payload["overall"], "issue_count": len(validation.issues)},
    )
    return ToolContractReport(path=path, payload=payload, issues=validation.issues)


def validate_tool_contracts(graph: dict[str, Any], run_id: str) -> ValidationResult:
    result = ValidationResult(ok=True)
    validate_contract_definitions(result)
    for item in graph.get("nodes", []) or []:
        if not isinstance(item, dict):
            result.add("tool_contract.invalid_node", "critical", "execution graph node must be an object")
            continue
        node_id = str(item.get("id", ""))
        node_type = str(item.get("type", ""))
        agent = str(item.get("agent", ""))
        contract = DEFAULT_TOOL_CONTRACTS.get(agent)
        if not contract:
            result.add("tool_contract.unknown_agent", "critical", f"no tool contract registered for agent: {agent}", node_id)
            continue
        if node_type not in contract.node_types:
            result.add(
                "tool_contract.node_type_not_allowed",
                "critical",
                f"{agent} is not allowed to execute node type: {node_type}",
                node_id,
            )
        required_artifacts = NODE_TYPE_ARTIFACT_TYPES.get(node_type, set())
        missing_artifacts = sorted(required_artifacts - contract.artifact_types)
        if missing_artifacts:
            result.add(
                "tool_contract.artifact_type_not_allowed",
                "critical",
                f"{agent} cannot write required artifact types for {node_type}: {missing_artifacts}",
                node_id,
            )
        rendered_write_paths = [render_run_path(path, run_id) for path in contract.write_paths]
        if required_artifacts and not any(path.startswith(f".loop/runs/{run_id}/") for path in rendered_write_paths):
            result.add(
                "tool_contract.missing_run_artifact_boundary",
                "critical",
                f"{agent} must be able to write run-local artifacts",
                node_id,
            )
    return result


def validate_node_result_artifacts(
    root: Path,
    run_id: str,
    node_payload: dict[str, Any],
    artifacts: list[dict[str, str]],
) -> ValidationResult:
    result = ValidationResult(ok=True)
    node_id = str(node_payload.get("id", ""))
    node_type = str(node_payload.get("type", ""))
    agent = str(node_payload.get("agent", ""))
    contract = DEFAULT_TOOL_CONTRACTS.get(agent)
    if contract is None:
        result.add("handler_result.unknown_agent", "critical", f"no tool contract registered for agent: {agent}", node_id)
        return result
    if node_type not in contract.node_types:
        result.add("handler_result.node_type_not_allowed", "critical", f"{agent} is not allowed to execute node type: {node_type}", node_id)

    returned_types = {artifact.get("type") for artifact in artifacts if isinstance(artifact, dict)}
    required_types = NODE_TYPE_ARTIFACT_TYPES.get(node_type, set())
    missing_required = sorted(required_types - returned_types)
    if missing_required:
        result.add(
            "handler_result.missing_required_artifact",
            "critical",
            f"{node_id} did not return required artifact types: {missing_required}",
            node_id,
        )

    allowed_write_paths = [render_run_path(path, run_id) for path in contract.write_paths]
    for artifact in artifacts:
        artifact_type = artifact.get("type")
        artifact_path = artifact.get("path")
        if not artifact_type:
            result.add("handler_result.missing_artifact_type", "critical", f"{node_id} returned artifact without type", node_id)
            continue
        if artifact_type not in contract.artifact_types:
            result.add(
                "handler_result.artifact_type_not_allowed",
                "critical",
                f"{agent} is not allowed to write artifact type: {artifact_type}",
                node_id,
            )
        if not artifact_path:
            result.add("handler_result.missing_artifact_path", "critical", f"{node_id} returned artifact without path", node_id)
            continue
        if is_unsafe_relative_path(artifact_path):
            result.add(
                "handler_result.unsafe_artifact_path",
                "critical",
                f"{node_id} returned unsafe artifact path: {artifact_path}",
                node_id,
            )
            continue
        if not any(fnmatch(artifact_path, pattern) for pattern in allowed_write_paths):
            result.add(
                "handler_result.artifact_path_not_allowed",
                "critical",
                f"{agent} is not allowed to write artifact path: {artifact_path}",
                node_id,
            )
        if not (root / artifact_path).is_file():
            result.add(
                "handler_result.artifact_missing",
                "critical",
                f"{node_id} returned artifact path that does not exist: {artifact_path}",
                node_id,
            )
    return result


def validate_contract_definitions(result: ValidationResult) -> None:
    for agent, contract in DEFAULT_TOOL_CONTRACTS.items():
        if contract.agent != agent:
            result.add("tool_contract.agent_key_mismatch", "critical", f"contract key does not match agent: {agent}")
        if not contract.allowed_tools:
            result.add("tool_contract.empty_tools", "critical", f"{agent} must declare allowed tools")
        if not contract.read_paths:
            result.add("tool_contract.empty_read_paths", "critical", f"{agent} must declare read paths")
        if not contract.write_paths:
            result.add("tool_contract.empty_write_paths", "critical", f"{agent} must declare write paths")
        if not contract.node_types:
            result.add("tool_contract.empty_node_types", "critical", f"{agent} must declare node types")
        for path in contract.write_paths:
            if path.startswith("/") or ".." in Path(path).parts:
                result.add("tool_contract.unsafe_write_path", "critical", f"{agent} has unsafe write path: {path}")


def node_contract_entries(graph: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    entries = []
    for item in graph.get("nodes", []) or []:
        if not isinstance(item, dict):
            continue
        agent = str(item.get("agent", ""))
        contract = DEFAULT_TOOL_CONTRACTS.get(agent)
        required_artifacts = sorted(NODE_TYPE_ARTIFACT_TYPES.get(str(item.get("type", "")), set()))
        entry = {
            "node_id": item.get("id"),
            "node_type": item.get("type"),
            "agent": agent,
            "required_artifact_types": required_artifacts,
            "status": "MISSING_CONTRACT" if contract is None else "BOUND",
        }
        if contract is not None:
            entry["contract"] = render_contract(contract, run_id)
        entries.append(entry)
    return entries


def render_contract(contract: ToolContract, run_id: str) -> dict[str, Any]:
    payload = contract.to_dict()
    payload["read_paths"] = [render_run_path(path, run_id) for path in contract.read_paths]
    payload["write_paths"] = [render_run_path(path, run_id) for path in contract.write_paths]
    return payload


def render_run_path(path: str, run_id: str) -> str:
    return path.replace("{run_id}", run_id)


def is_unsafe_relative_path(path: str) -> bool:
    item = Path(path)
    return item.is_absolute() or ".." in item.parts


def issue_to_dict(issue: ValidationIssue) -> dict[str, str | None]:
    return {
        "code": issue.code,
        "severity": issue.severity,
        "message": issue.message,
        "path": issue.path,
    }
