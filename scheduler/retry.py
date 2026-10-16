from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import EventLogger
from .runs import GraphRunStore, read_json, relative_to_root
from .state import ValidationIssue


@dataclass(frozen=True)
class InvalidationResult:
    run_id: str
    reset_node: str
    invalidated_nodes: list[str] = field(default_factory=list)
    removed_artifacts: list[str] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


class RetryManager:
    def __init__(self, root: Path):
        self.root = root
        self.store = GraphRunStore(root)
        self.events = EventLogger(root)

    def reset_node(self, run_id: str, node_id: str) -> InvalidationResult:
        graph = read_json(self.store.run_dir(run_id) / "execution-graph.json")
        node_ids = {node["id"] for node in graph.get("nodes", []) or []}
        if node_id not in node_ids:
            return InvalidationResult(
                run_id=run_id,
                reset_node=node_id,
                issues=[
                    ValidationIssue(
                        code="retry.unknown_node",
                        severity="critical",
                        message=f"cannot reset unknown node: {node_id}",
                    )
                ],
            )

        state = self.store.read_state(run_id)
        invalidated = sorted(downstream_nodes(graph, node_id) | {node_id}, key=lambda item: graph_order(graph).get(item, 0))
        invalidated_set = set(invalidated)
        state.completed_nodes = [item for item in state.completed_nodes if item not in invalidated_set]
        state.failed_nodes = [item for item in state.failed_nodes if item not in invalidated_set]
        state.blocked_nodes = [item for item in state.blocked_nodes if item not in invalidated_set]
        state.current_nodes = [item for item in state.current_nodes if item not in invalidated_set]
        state.status = "RUNNING"
        state.last_error = None
        state.retry_count[node_id] = int(state.retry_count.get(node_id, 0) or 0) + 1

        removed = self.remove_node_artifacts(run_id, invalidated_set)
        state.artifact_refs = [
            ref
            for ref in state.artifact_refs
            if ref.get("path") not in set(removed) and artifact_node_id(ref.get("path", "")) not in invalidated_set
        ]

        ready = ready_nodes(graph, set(state.completed_nodes), set(state.failed_nodes))
        state.current_nodes = ready
        remaining = {node["id"] for node in graph["nodes"]} - set(state.completed_nodes) - set(state.failed_nodes)
        state.blocked_nodes = sorted(remaining - set(ready), key=lambda item: graph_order(graph).get(item, 0))
        self.store.write_state(state, f"{len(state.completed_nodes):03d}-{node_id}-reset.json")
        self.events.append_run(
            run_id,
            "node_invalidated",
            {
                "run_id": run_id,
                "node_id": node_id,
                "invalidated_nodes": invalidated,
                "removed_artifacts": removed,
                "retry_count": state.retry_count[node_id],
            },
        )
        return InvalidationResult(run_id=run_id, reset_node=node_id, invalidated_nodes=invalidated, removed_artifacts=removed)

    def remove_node_artifacts(self, run_id: str, node_ids: set[str]) -> list[str]:
        removed: list[str] = []
        run_dir = self.store.run_dir(run_id)
        for node_id in node_ids:
            path = run_dir / "artifacts" / f"{node_id}.json"
            if path.exists():
                path.unlink()
                removed.append(relative_to_root(self.root, path))
        for path in [run_dir / "conformance-report.json", run_dir / "spec-change-request.json"]:
            if path.exists() and should_remove_run_level_artifact(path.name, node_ids):
                path.unlink()
                removed.append(relative_to_root(self.root, path))
        return sorted(removed)


def downstream_nodes(graph: dict[str, Any], node_id: str) -> set[str]:
    adjacency: dict[str, list[str]] = {}
    for edge in graph.get("edges", []) or []:
        adjacency.setdefault(edge.get("source"), []).append(edge.get("target"))
    seen: set[str] = set()
    stack = list(adjacency.get(node_id, []))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(adjacency.get(current, []))
    return seen


def ready_nodes(graph: dict[str, Any], completed: set[str], failed: set[str]) -> list[str]:
    incoming = incoming_edges(graph)
    ordered = graph_order(graph)
    ready = [
        node["id"]
        for node in graph.get("nodes", []) or []
        if node["id"] not in completed
        and node["id"] not in failed
        and all(source in completed for source in incoming.get(node["id"], []))
    ]
    return sorted(ready, key=lambda item: ordered.get(item, 0))


def incoming_edges(graph: dict[str, Any]) -> dict[str, list[str]]:
    incoming = {node["id"]: [] for node in graph.get("nodes", []) or []}
    for edge in graph.get("edges", []) or []:
        incoming.setdefault(edge.get("target"), []).append(edge.get("source"))
    return incoming


def graph_order(graph: dict[str, Any]) -> dict[str, int]:
    return {node["id"]: index for index, node in enumerate(graph.get("nodes", []) or [])}


def artifact_node_id(path: str) -> str | None:
    name = Path(path).name
    if not name.endswith(".json"):
        return None
    return name[:-5]


def should_remove_run_level_artifact(name: str, node_ids: set[str]) -> bool:
    if name == "conformance-report.json":
        return "conformance_node" in node_ids
    if name == "spec-change-request.json":
        return "conformance_node" in node_ids
    return False
