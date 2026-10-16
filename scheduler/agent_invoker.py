from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runs import GraphRunStore, RunState, relative_to_root, write_json
from .tool_contracts import DEFAULT_TOOL_CONTRACTS, render_contract
from .state import ValidationIssue


@dataclass(frozen=True)
class AgentContextPack:
    path: Path | None
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)


def build_agent_context_pack(
    root: Path,
    run_id: str,
    graph: dict[str, Any],
    state: RunState,
    node_payload: dict[str, Any],
) -> AgentContextPack:
    agent = str(node_payload.get("agent", ""))
    contract = DEFAULT_TOOL_CONTRACTS.get(agent)
    if contract is None:
        return AgentContextPack(
            path=None,
            payload={},
            issues=[
                ValidationIssue(
                    code="agent_context.missing_tool_contract",
                    severity="critical",
                    message=f"no tool contract registered for agent: {agent}",
                    path=str(node_payload.get("id", "")),
                )
            ],
        )

    store = GraphRunStore(root)
    run_dir = store.run_dir(run_id)
    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "node": {
            "id": node_payload.get("id"),
            "type": node_payload.get("type"),
            "label": node_payload.get("label"),
            "agent": agent,
            "requirements": list(node_payload.get("requirements", []) or []),
            "acceptance_criteria": list(node_payload.get("acceptance_criteria", []) or []),
            "work_package_id": node_payload.get("work_package_id"),
        },
        "bound_spec": state.bound_spec.to_dict(),
        "contract": render_contract(contract, run_id),
        "input_refs": {
            "spec_snapshot": state.bound_spec.snapshot_uri,
            "run_state": relative_to_root(root, run_dir / "run-state.json"),
            "execution_plan": relative_to_root(root, run_dir / "execution-plan.json"),
            "execution_graph": relative_to_root(root, run_dir / "execution-graph.json"),
            "tool_contract_report": relative_to_root(root, run_dir / "tool-contract-report.json"),
            "completed_artifacts": list(state.artifact_refs),
        },
        "dependency_refs": dependency_artifact_refs(graph, state),
    }
    path = run_dir / "context-packs" / f"{node_payload['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)
    return AgentContextPack(path=path, payload=payload, issues=[])


def dependency_artifact_refs(graph: dict[str, Any], state: RunState) -> list[dict[str, str]]:
    completed = set(state.completed_nodes)
    dependency_nodes = {
        edge.get("source")
        for edge in graph.get("edges", []) or []
        if edge.get("target") in completed or edge.get("target") in state.current_nodes
    }
    refs = []
    for ref in state.artifact_refs:
        path = ref.get("path", "")
        node_id = artifact_node_id(path)
        if node_id in dependency_nodes or node_id in completed:
            refs.append(ref)
    return refs


def artifact_node_id(path: str) -> str | None:
    name = Path(path).name
    if name.endswith(".json"):
        return name[:-5]
    return None
