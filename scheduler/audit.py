from __future__ import annotations

from pathlib import Path

from .artifacts import ArtifactManager
from .schema import validate_done_transition, validate_task_card
from .state import ValidationIssue
from .task_card import find_task_cards, load_task_card


def audit_repository(root: Path) -> list[ValidationIssue]:
    artifacts = ArtifactManager(root)
    issues: list[ValidationIssue] = []
    for path in find_task_cards(root):
        task = load_task_card(path)
        base = validate_task_card(task)
        issues.extend(base.issues)
        done = validate_done_transition(task, artifacts)
        issues.extend(issue for issue in done.issues if issue not in base.issues)
    return issues


def format_audit(issues: list[ValidationIssue]) -> str:
    if not issues:
        return "GraphFlow audit passed: no task-card, artifact, or DONE-transition violations found."
    lines = ["GraphFlow audit found violations:"]
    for issue in issues:
        location = f" ({issue.path})" if issue.path else ""
        lines.append(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
    return "\n".join(lines)

