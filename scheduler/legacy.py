from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runs import relative_to_root, write_json
from .spec_flow import validate_change_spec_payload
from .state import TaskCard, ValidationIssue
from .task_card import load_task_card


@dataclass(frozen=True)
class LegacyConversionResult:
    path: Path | None
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)


def convert_task_card_to_change_spec(root: Path, task_card_path: Path) -> LegacyConversionResult:
    task = load_task_card(task_card_path)
    payload = build_change_spec_from_task_card(root, task)
    result = validate_change_spec_payload(payload, task_card_path)
    issues = list(result.issues)
    if not task.done_items:
        issues.append(
            ValidationIssue(
                code="legacy_task.no_done_items",
                severity="critical",
                message="legacy task card must contain Done items to become ChangeSpec acceptance criteria",
                path=str(task_card_path),
            )
        )
    if issues:
        return LegacyConversionResult(path=None, payload=payload, issues=issues)

    target = root / "specs" / task.project / "drafts" / f"{task.id}-from-task-card-change-spec.json"
    if target.exists():
        return LegacyConversionResult(
            path=None,
            payload=payload,
            issues=[
                ValidationIssue(
                    code="legacy_task.change_spec_exists",
                    severity="critical",
                    message="legacy ChangeSpec draft already exists",
                    path=str(target),
                )
            ],
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, payload)
    return LegacyConversionResult(path=target, payload=payload, issues=[])


def build_change_spec_from_task_card(root: Path, task: TaskCard) -> dict[str, Any]:
    requirement_id = f"req-{slug(task.id)}"
    return {
        "schema_version": "1.0",
        "spec_id": task.id,
        "project": task.project,
        "intent": {
            "summary": task.title or f"Legacy task {task.id}",
            "source": "legacy-task-card",
            "goal": extract_section_text(task.body, "Goal"),
        },
        "scope": {
            "in": [task.title or task.id],
            "out": [],
        },
        "requirements": [
            {
                "id": requirement_id,
                "summary": extract_section_text(task.body, "Goal") or task.title or task.id,
                "source": "legacy-task-card",
            }
        ],
        "acceptance_criteria": [
            {
                "id": item.id,
                "requirement_id": requirement_id,
                "summary": f"Legacy Done item {item.id}",
                "check": item.check,
                "expect": item.expect,
                "gate_stage": item.gate_stage,
            }
            for item in task.done_items
        ],
        "constraints": [],
        "dependencies": list(task.raw_frontmatter.get("dependencies", []) or []),
        "assumptions": [
            {
                "source": "legacy-task-card",
                "status": task.status,
                "phase": task.phase,
            }
        ],
        "open_questions": [],
        "product_shape": {
            "product_type": task.project_type,
            "source": "legacy-task-card",
        },
        "source_refs": [relative_to_root(root, task.path)],
    }


def extract_section_text(body: str, heading: str) -> str:
    lines = body.splitlines()
    target = f"## {heading}".lower()
    collecting = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("## "):
            if collecting:
                break
            collecting = stripped.lower().startswith(target)
            continue
        if collecting:
            collected.append(line.rstrip())
    return "\n".join(line for line in collected if line.strip()).strip()


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-").lower()
    return cleaned or "legacy"
