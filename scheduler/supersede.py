from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .events import EventLogger
from .runs import GraphRunStore, RunState
from .specs import SpecRef, load_spec_snapshot
from .state import ValidationIssue


@dataclass(frozen=True)
class SupersedeResult:
    old_run_id: str
    new_run_id: str | None = None
    old_status: str | None = None
    new_snapshot_uri: str | None = None
    issues: list[ValidationIssue] = field(default_factory=list)


class SupersedeManager:
    def __init__(self, root: Path):
        self.root = root
        self.store = GraphRunStore(root)
        self.events = EventLogger(root)

    def supersede(self, old_run_id: str, new_snapshot_path: Path, new_run_id: str | None = None) -> SupersedeResult:
        old_state = self.store.read_state(old_run_id)
        snapshot = load_spec_snapshot(new_snapshot_path)
        issues = list(snapshot.issues)
        if issues:
            return SupersedeResult(old_run_id=old_run_id, old_status=old_state.status, issues=issues)

        if old_state.status == "SUPERSEDED":
            issues.append(
                ValidationIssue(
                    code="supersede.already_superseded",
                    severity="critical",
                    message=f"GraphRun is already superseded: {old_run_id}",
                    path=str(self.store.run_dir(old_run_id)),
                )
            )
        if snapshot.spec_id != old_state.bound_spec.spec_id:
            issues.append(
                ValidationIssue(
                    code="supersede.spec_mismatch",
                    severity="critical",
                    message="new SpecSnapshot spec_id must match old GraphRun bound spec_id",
                    path=str(new_snapshot_path),
                )
            )
        if snapshot.version <= old_state.bound_spec.version:
            issues.append(
                ValidationIssue(
                    code="supersede.version_not_newer",
                    severity="critical",
                    message="new SpecSnapshot version must be newer than old GraphRun bound version",
                    path=str(new_snapshot_path),
                )
            )
        selected_new_run_id = new_run_id or default_superseding_run_id(old_run_id, snapshot.spec_id, snapshot.version)
        if self.store.run_dir(selected_new_run_id).exists():
            issues.append(
                ValidationIssue(
                    code="supersede.new_run_exists",
                    severity="critical",
                    message=f"new GraphRun already exists: {selected_new_run_id}",
                    path=str(self.store.run_dir(selected_new_run_id)),
                )
            )
        if issues:
            return SupersedeResult(old_run_id=old_run_id, old_status=old_state.status, issues=issues)

        new_state = self.store.create(new_snapshot_path, run_id=selected_new_run_id)
        self.mark_old_run_superseded(old_state, new_state, snapshot.spec_ref(self.root))
        return SupersedeResult(
            old_run_id=old_run_id,
            new_run_id=new_state.run_id,
            old_status="SUPERSEDED",
            new_snapshot_uri=new_state.bound_spec.snapshot_uri,
        )

    def mark_old_run_superseded(self, old_state: RunState, new_state: RunState, new_spec_ref: SpecRef) -> None:
        old_state.status = "SUPERSEDED"
        old_state.current_nodes = []
        old_state.blocked_nodes = []
        old_state.last_error = f"superseded by {new_state.run_id}"
        self.store.write_state(old_state, "999-superseded.json")
        self.events.append_run(
            old_state.run_id,
            "run_superseded",
            {
                "run_id": old_state.run_id,
                "new_run_id": new_state.run_id,
                "new_spec_id": new_spec_ref.spec_id,
                "new_spec_version": new_spec_ref.version,
                "new_content_hash": new_spec_ref.content_hash,
                "new_snapshot_uri": new_spec_ref.snapshot_uri,
            },
        )
        self.events.append_run(
            new_state.run_id,
            "run_supersedes",
            {
                "run_id": new_state.run_id,
                "superseded_run_id": old_state.run_id,
                "superseded_spec_id": old_state.bound_spec.spec_id,
                "superseded_spec_version": old_state.bound_spec.version,
                "superseded_content_hash": old_state.bound_spec.content_hash,
            },
        )


def default_superseding_run_id(old_run_id: str, spec_id: str, version: int) -> str:
    return f"run-{spec_id}-v{version}-supersedes-{old_run_id}"
