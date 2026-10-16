from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runs import GraphRunStore, RunState, relative_to_root, utc_now, write_json
from .state import ValidationIssue, ValidationResult


SCR_TYPES = {"spec_gap", "spec_conflict", "infeasible", "scope_change", "acceptance_bug"}
SCR_REQUIRED_FIELDS = [
    "schema_version",
    "run_id",
    "spec_id",
    "base_version",
    "raised_by",
    "type",
    "reason",
    "affected_requirements",
    "affected_acceptance_criteria",
    "affected_nodes",
    "evidence_refs",
    "created_at",
]


@dataclass(frozen=True)
class SpecChangeRequest:
    path: Path | None
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)


def create_spec_change_request(
    root: Path,
    state: RunState,
    raised_by: str,
    request_type: str,
    reason: str,
    affected_nodes: list[str],
    evidence_refs: list[str],
    conformance_report: dict[str, Any] | None = None,
) -> SpecChangeRequest:
    affected_requirements: list[str] = []
    affected_acceptance: list[str] = []
    if conformance_report is not None:
        for item in conformance_report.get("unverified", []) or []:
            if item.get("type") == "requirement":
                affected_requirements.append(str(item.get("id")))
            if item.get("type") == "acceptance":
                affected_acceptance.append(str(item.get("id")))
        evidence_refs.extend(conformance_report.get("evidence_refs", []) or [])

    payload = {
        "schema_version": "1.0",
        "run_id": state.run_id,
        "spec_id": state.bound_spec.spec_id,
        "base_version": state.bound_spec.version,
        "raised_by": raised_by,
        "type": request_type,
        "reason": reason,
        "affected_requirements": sorted(set(affected_requirements)),
        "affected_acceptance_criteria": sorted(set(affected_acceptance)),
        "affected_nodes": sorted(set(affected_nodes)),
        "evidence_refs": sorted(set(evidence_refs)),
        "created_at": utc_now(),
    }
    result = validate_spec_change_request_payload(payload)
    if not result.ok:
        return SpecChangeRequest(path=None, payload=payload, issues=result.issues)

    store = GraphRunStore(root)
    path = store.run_dir(state.run_id) / "spec-change-request.json"
    write_json(path, payload)
    return SpecChangeRequest(path=path, payload=payload, issues=[])


def validate_spec_change_request_payload(payload: dict[str, Any]) -> ValidationResult:
    result = ValidationResult(ok=True)
    for field_name in SCR_REQUIRED_FIELDS:
        if payload.get(field_name) in (None, ""):
            result.add("spec_change_request.missing_field", "error", f"missing SpecChangeRequest field: {field_name}")

    if payload.get("type") not in SCR_TYPES:
        result.add("spec_change_request.invalid_type", "error", f"invalid SpecChangeRequest type: {payload.get('type')}")

    if not isinstance(payload.get("base_version"), int) or payload.get("base_version", 0) < 1:
        result.add("spec_change_request.invalid_base_version", "error", "base_version must be a positive integer")

    for field_name in ["affected_requirements", "affected_acceptance_criteria", "affected_nodes", "evidence_refs"]:
        if field_name in payload and not isinstance(payload[field_name], list):
            result.add("spec_change_request.invalid_list", "error", f"{field_name} must be a list")

    return result


def scr_artifact_ref(root: Path, path: Path) -> dict[str, str]:
    return {
        "type": "spec-change-request",
        "path": relative_to_root(root, path),
        "description": "SpecChangeRequest raised by GraphFlow",
    }
