from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import EventLogger
from .specs import SpecRef, SpecSnapshot, load_spec_snapshot
from .state import ValidationIssue, ValidationResult


RUN_STATUSES = {
    "CREATED",
    "BOUND",
    "PLANNING",
    "RUNNING",
    "AWAITING_HUMAN",
    "AWAITING_SPEC_CHANGE",
    "SUPERSEDED",
    "DONE",
    "FAILED",
    "ESCALATED",
}

# Mode A (GraphFlow) 合法状态转换表。
# 与 state.py:LEGAL_TRANSITIONS (Mode B 遗留 TaskCard 状态机) 完全独立。
# 本表描述 RunState 的实际转换路径，由 executor 状态机 + CLI 恢复命令驱动：
#   BOUND         → PLANNING (plan), RUNNING (direct execute)
#   PLANNING      → RUNNING, FAILED
#   RUNNING       → AWAITING_HUMAN (l3_review), AWAITING_SPEC_CHANGE (spec_change),
#                   DONE, FAILED, ESCALATED
#   AWAITING_HUMAN       → RUNNING (resume_human_review APPROVE), ESCALATED (REJECT)
#   AWAITING_SPEC_CHANGE → BOUND (resume_spec_change, rebind + reset)
#   DONE/FAILED/ESCALATED/SUPERSEDED → terminal
RUN_LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"BOUND"},
    "BOUND": {"PLANNING", "RUNNING"},
    "PLANNING": {"RUNNING", "FAILED"},
    "RUNNING": {"AWAITING_HUMAN", "AWAITING_SPEC_CHANGE", "DONE", "FAILED", "ESCALATED"},
    "AWAITING_HUMAN": {"RUNNING", "ESCALATED"},
    "AWAITING_SPEC_CHANGE": {"BOUND"},
    "SUPERSEDED": set(),
    "DONE": set(),
    "FAILED": set(),
    "ESCALATED": set(),
}


def is_legal_run_transition(from_status: str, to_status: str) -> bool:
    """Check if a RunState status transition is legal per RUN_LEGAL_TRANSITIONS."""
    return to_status in RUN_LEGAL_TRANSITIONS.get(from_status, set())


@dataclass(frozen=True)
class SpecBinding:
    run_id: str
    bound_at: str
    bound_spec: SpecRef

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "bound_at": self.bound_at,
            "bound_spec": self.bound_spec.to_dict(),
        }


@dataclass
class RunState:
    run_id: str
    status: str
    bound_spec: SpecRef
    current_nodes: list[str] = field(default_factory=list)
    completed_nodes: list[str] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)
    blocked_nodes: list[str] = field(default_factory=list)
    retry_count: dict[str, int] = field(default_factory=dict)
    artifact_refs: list[dict[str, str]] = field(default_factory=list)
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "bound_spec": self.bound_spec.to_dict(),
            "current_nodes": self.current_nodes,
            "completed_nodes": self.completed_nodes,
            "failed_nodes": self.failed_nodes,
            "blocked_nodes": self.blocked_nodes,
            "retry_count": self.retry_count,
            "artifact_refs": self.artifact_refs,
            "last_error": self.last_error,
        }


class GraphRunStore:
    def __init__(self, root: Path):
        self.root = root
        self.runs_dir = root / ".loop" / "runs"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def create(self, snapshot_path: Path, run_id: str | None = None) -> RunState:
        snapshot = load_spec_snapshot(snapshot_path)
        if snapshot.issues:
            raise ValueError(format_issues("Cannot bind invalid SpecSnapshot", snapshot.issues))

        selected_run_id = run_id or new_run_id(snapshot)
        run_dir = self.run_dir(selected_run_id)
        if run_dir.exists():
            raise FileExistsError(f"GraphRun already exists: {run_dir}")

        run_dir.mkdir(parents=True)
        (run_dir / "artifacts").mkdir()
        (run_dir / "state-snapshots").mkdir()

        spec_ref = snapshot.spec_ref(self.root)
        now = utc_now()
        binding = SpecBinding(run_id=selected_run_id, bound_at=now, bound_spec=spec_ref)
        state = RunState(run_id=selected_run_id, status="BOUND", bound_spec=spec_ref, current_nodes=["spec_binding_node"])

        write_json(run_dir / "spec-binding.json", binding.to_dict())
        write_json(run_dir / "run-state.json", state.to_dict())
        write_json(run_dir / "state-snapshots" / "000-created.json", state.to_dict())

        events = EventLogger(self.root)
        events.append_run(selected_run_id, "run_created", {"run_id": selected_run_id})
        events.append_run(
            selected_run_id,
            "spec_bound",
            {
                "run_id": selected_run_id,
                "spec_id": spec_ref.spec_id,
                "spec_version": spec_ref.version,
                "content_hash": spec_ref.content_hash,
                "snapshot_uri": spec_ref.snapshot_uri,
            },
        )
        return state

    def rebind_spec(self, run_id: str, new_snapshot_path: Path) -> RunState:
        """Rebind an existing GraphRun to a newer SpecSnapshot version.

        Used when resuming from ``AWAITING_SPEC_CHANGE``: the spec owner has
        published a new version (via SCR → revision draft → approve → publish),
        and the *same* run is rebound to it rather than creating a new run.
        Validates that the new snapshot is the same spec_id with a strictly
        higher version.
        """
        snapshot = load_spec_snapshot(new_snapshot_path)
        if snapshot.issues:
            raise ValueError(format_issues("Cannot rebind to invalid SpecSnapshot", snapshot.issues))

        state = self.read_state(run_id)
        old_spec_ref = state.bound_spec
        new_spec_ref = snapshot.spec_ref(self.root)

        if new_spec_ref.spec_id != old_spec_ref.spec_id:
            raise ValueError(
                f"Cannot rebind: spec_id mismatch "
                f"({old_spec_ref.spec_id} -> {new_spec_ref.spec_id})"
            )
        if new_spec_ref.version <= old_spec_ref.version:
            raise ValueError(
                f"Cannot rebind: new version {new_spec_ref.version} "
                f"must be > current version {old_spec_ref.version}"
            )

        binding_path = self.run_dir(run_id) / "spec-binding.json"
        binding = read_json(binding_path)
        binding["bound_spec"] = new_spec_ref.to_dict()
        binding["rebound_at"] = utc_now()
        write_json(binding_path, binding)

        state.bound_spec = new_spec_ref
        self.write_state(state)

        events = EventLogger(self.root)
        events.append_run(
            run_id,
            "spec_rebound",
            {
                "run_id": run_id,
                "old_spec_id": old_spec_ref.spec_id,
                "old_version": old_spec_ref.version,
                "old_content_hash": old_spec_ref.content_hash,
                "new_spec_id": new_spec_ref.spec_id,
                "new_version": new_spec_ref.version,
                "new_content_hash": new_spec_ref.content_hash,
                "new_snapshot_uri": new_spec_ref.snapshot_uri,
            },
        )
        return state

    def read_state(self, run_id: str) -> RunState:
        payload = read_json(self.run_dir(run_id) / "run-state.json")
        spec = payload["bound_spec"]
        return RunState(
            run_id=payload["run_id"],
            status=payload["status"],
            bound_spec=SpecRef(
                spec_id=spec["spec_id"],
                version=int(spec["version"]),
                content_hash=spec["content_hash"],
                snapshot_uri=spec["snapshot_uri"],
            ),
            current_nodes=list(payload.get("current_nodes", []) or []),
            completed_nodes=list(payload.get("completed_nodes", []) or []),
            failed_nodes=list(payload.get("failed_nodes", []) or []),
            blocked_nodes=list(payload.get("blocked_nodes", []) or []),
            retry_count=dict(payload.get("retry_count", {}) or {}),
            artifact_refs=list(payload.get("artifact_refs", []) or []),
            last_error=payload.get("last_error"),
        )

    def write_state(self, state: RunState, snapshot_name: str | None = None) -> None:
        run_dir = self.run_dir(state.run_id)
        write_json(run_dir / "run-state.json", state.to_dict())
        if snapshot_name is not None:
            write_json(run_dir / "state-snapshots" / snapshot_name, state.to_dict())

    def append_artifact_ref(self, state: RunState, artifact_type: str, path: Path, description: str) -> None:
        state.artifact_refs.append(
            {
                "type": artifact_type,
                "path": relative_to_root(self.root, path),
                "description": description,
            }
        )

    def validate(self, run_id: str) -> ValidationResult:
        result = ValidationResult(ok=True)
        run_dir = self.run_dir(run_id)
        for name in ["spec-binding.json", "run-state.json", "events.jsonl"]:
            path = run_dir / name
            if not path.is_file():
                result.add("graph_run.missing_file", "critical", f"missing GraphRun file: {name}", str(path))

        binding_path = run_dir / "spec-binding.json"
        state_path = run_dir / "run-state.json"
        if not binding_path.is_file() or not state_path.is_file():
            return result

        binding = read_json(binding_path)
        state = read_json(state_path)
        if binding.get("run_id") != state.get("run_id"):
            result.add("graph_run.run_id_mismatch", "critical", "spec-binding.json and run-state.json run_id differ", str(run_dir))

        status = state.get("status")
        if status not in RUN_STATUSES:
            result.add("graph_run.invalid_status", "error", f"unknown GraphRun status: {status}", str(state_path))

        bound_from_binding = binding.get("bound_spec", {})
        bound_from_state = state.get("bound_spec", {})
        if bound_from_binding != bound_from_state:
            result.add("graph_run.spec_binding_mismatch", "critical", "RunState must match spec-binding.json", str(run_dir))

        snapshot_uri = bound_from_binding.get("snapshot_uri")
        if snapshot_uri:
            snapshot_path = (self.root / snapshot_uri).resolve()
            if not snapshot_path.is_file():
                result.add("graph_run.snapshot_missing", "critical", "bound SpecSnapshot does not exist", str(snapshot_path))
            else:
                snapshot = load_spec_snapshot(snapshot_path)
                result.issues.extend(snapshot.issues)
                if snapshot.issues:
                    result.ok = False
                if bound_from_binding.get("content_hash") != snapshot.content_hash:
                    result.add("graph_run.bound_hash_mismatch", "critical", "bound content_hash differs from snapshot", str(binding_path))

        return result


def new_run_id(snapshot: SpecSnapshot) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"run-{snapshot.spec_id}-v{snapshot.version}-{timestamp}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def format_issues(prefix: str, issues: list[ValidationIssue]) -> str:
    details = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
    return f"{prefix}: {details}"
