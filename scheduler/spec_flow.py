from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runs import utc_now, write_json
from .specs import STRICT_SCHEMA_VERSION, calculate_content_hash, load_spec_snapshot, relative_uri, validate_strict_requirement_acceptance
from .state import ValidationIssue, ValidationResult


CHANGE_SPEC_REQUIRED_FIELDS = [
    "schema_version",
    "spec_id",
    "project",
    "intent",
    "scope",
    "requirements",
    "acceptance_criteria",
    "constraints",
    "dependencies",
    "assumptions",
    "open_questions",
    "product_shape",
    "source_refs",
]

PRODUCT_SPEC_REQUIRED_FIELDS = [
    "schema_version",
    "project",
    "product_type",
    "target_users",
    "core_workflows",
    "constraints",
    "glossary",
]

APPROVAL_STATUSES = {"APPROVED", "REJECTED"}


@dataclass(frozen=True)
class ChangeSpecDraft:
    path: Path
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class ProductSpec:
    path: Path
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class PublishResult:
    path: Path | None
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class RevisionDraftResult:
    path: Path | None
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class ApprovalResult:
    path: Path | None
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)


def load_change_spec(path: Path) -> ChangeSpecDraft:
    payload = read_json(path)
    result = validate_change_spec_payload(payload, path)
    return ChangeSpecDraft(path=path, payload=payload, issues=result.issues)


def load_product_spec(path: Path) -> ProductSpec:
    payload = read_json(path)
    result = validate_product_spec_payload(payload, path)
    return ProductSpec(path=path, payload=payload, issues=result.issues)


def validate_change_spec_payload(payload: dict[str, Any], path: Path | None = None) -> ValidationResult:
    result = ValidationResult(ok=True)
    location = str(path) if path else None
    for field_name in CHANGE_SPEC_REQUIRED_FIELDS:
        if payload.get(field_name) in (None, ""):
            result.add("change_spec.missing_field", "error", f"missing ChangeSpec field: {field_name}", location)

    for field_name in ["requirements", "acceptance_criteria", "constraints", "dependencies", "assumptions", "open_questions", "source_refs"]:
        if field_name in payload and not isinstance(payload[field_name], list):
            result.add("change_spec.invalid_list", "error", f"{field_name} must be a list", location)

    for field_name in ["intent", "scope", "product_shape", "approval"]:
        if field_name in payload and not isinstance(payload[field_name], dict):
            result.add("change_spec.invalid_object", "error", f"{field_name} must be an object", location)

    # Discovery & Assumptions 结构化校验（opt-in）：
    # 仅当 assumption 项为 dict 且含 category 字段时触发结构化风险校验。
    # 存量/legacy assumptions（string 或无 category 的 dict）不触发，保持向后兼容。
    ASSUMPTION_CATEGORIES = {"value", "usability", "viability", "feasibility", "ethics", "go_to_market", "strategy", "team"}
    ASSUMPTION_CONFIDENCE = {"high", "medium", "low"}
    ASSUMPTION_PRIORITIES = {"high_impact_low_risk", "high_impact_high_risk", "low_impact_low_risk", "low_impact_high_risk"}
    assumptions = payload.get("assumptions", [])
    if isinstance(assumptions, list):
        for idx, asm in enumerate(assumptions):
            if not isinstance(asm, dict):
                continue
            cat = asm.get("category")
            if cat is None:
                continue  # opt-in: 无 category 字段，不触发结构化校验
            if cat not in ASSUMPTION_CATEGORIES:
                result.add(
                    "change_spec.invalid_assumption_category",
                    "error",
                    f"assumption[{idx}].category must be one of {sorted(ASSUMPTION_CATEGORIES)}, got '{cat}'",
                    location,
                )
            conf = asm.get("confidence")
            if conf is not None and conf not in ASSUMPTION_CONFIDENCE:
                result.add(
                    "change_spec.invalid_assumption_confidence",
                    "error",
                    f"assumption[{idx}].confidence must be one of {sorted(ASSUMPTION_CONFIDENCE)}, got '{conf}'",
                    location,
                )
            pri = asm.get("priority")
            if pri is not None and pri not in ASSUMPTION_PRIORITIES:
                result.add(
                    "change_spec.invalid_assumption_priority",
                    "error",
                    f"assumption[{idx}].priority must be one of {sorted(ASSUMPTION_PRIORITIES)}, got '{pri}'",
                    location,
                )
            # high_impact_high_risk 必须有 experiment
            if pri == "high_impact_high_risk" and not asm.get("experiment"):
                result.add(
                    "change_spec.missing_experiment",
                    "error",
                    f"assumption[{idx}] with priority=high_impact_high_risk must have an experiment",
                    location,
                )

    # Discovery 元数据校验（opt-in）：仅当含 discovery 字段时触发
    discovery = payload.get("discovery")
    if isinstance(discovery, dict):
        skills_executed = discovery.get("skills_executed")
        if not isinstance(skills_executed, list) or not skills_executed:
            result.add(
                "change_spec.discovery_no_skills",
                "error",
                "discovery.skills_executed must be a non-empty list when discovery field is present",
                location,
            )

    # Job Story 结构化校验（opt-in）：
    # 仅当 requirement 项含 job_story 字段时触发结构化校验。
    # 存量 requirement 无 job_story 字段，不触发，保持向后兼容。
    requirements = payload.get("requirements", [])
    if isinstance(requirements, list):
        for idx, req in enumerate(requirements):
            if not isinstance(req, dict):
                continue
            job_story = req.get("job_story")
            if job_story is None:
                continue  # opt-in: 无 job_story 字段，不触发校验
            if not isinstance(job_story, dict):
                result.add(
                    "change_spec.invalid_job_story",
                    "error",
                    f"requirement[{idx}].job_story must be an object",
                    location,
                )
                continue
            for sub_field in ("situation", "motivation", "outcome"):
                val = job_story.get(sub_field)
                if not isinstance(val, str) or not val.strip():
                    result.add(
                        "change_spec.invalid_job_story_field",
                        "error",
                        f"requirement[{idx}].job_story.{sub_field} must be a non-empty string",
                        location,
                    )

    if payload.get("open_questions"):
        result.add("change_spec.open_questions_unresolved", "critical", "open_questions must be resolved before publish", location)

    # PM Discussion 产物引用校验（create-prd 固化，opt-in）：
    # 仅当 ChangeSpec 包含 pm_discussion 字段时才校验 source_refs 引用 PRD。
    # 符合 opt-in 哲学——存量 ChangeSpec 无 pm_discussion 字段，不触发新校验。
    pm_discussion = payload.get("pm_discussion")
    if isinstance(pm_discussion, dict):
        source_refs = payload.get("source_refs", [])
        if isinstance(source_refs, list):
            has_prd_ref = any(
                isinstance(ref, str) and ("prd.md" in ref or "/prd" in ref)
                for ref in source_refs
            )
            if not has_prd_ref:
                result.add(
                    "change_spec.missing_prd_ref",
                    "critical",
                    "source_refs must reference the PRD (create-prd output, e.g. projects/{product}/prd.md)",
                    location,
                )

    if not payload.get("requirements"):
        result.add("change_spec.no_requirements", "critical", "at least one requirement is required", location)

    if not payload.get("acceptance_criteria"):
        result.add("change_spec.no_acceptance", "critical", "at least one acceptance criterion is required", location)

    if payload.get("schema_version") == STRICT_SCHEMA_VERSION:
        result.issues.extend(validate_strict_requirement_acceptance(payload, path).issues)

    return result


def validate_product_spec_payload(payload: dict[str, Any], path: Path | None = None) -> ValidationResult:
    result = ValidationResult(ok=True)
    location = str(path) if path else None
    for field_name in PRODUCT_SPEC_REQUIRED_FIELDS:
        if payload.get(field_name) in (None, ""):
            result.add("product_spec.missing_field", "error", f"missing ProductSpec field: {field_name}", location)
    for field_name in ["target_users", "core_workflows", "constraints", "glossary"]:
        if field_name in payload and not isinstance(payload[field_name], list):
            result.add("product_spec.invalid_list", "error", f"{field_name} must be a list", location)
    return result


def build_snapshot_payload(change_spec: ChangeSpecDraft, version: int, product_spec: ProductSpec | None = None) -> dict[str, Any]:
    payload = {
        "schema_version": change_spec.payload.get("schema_version", "1.0"),
        "spec_id": change_spec.payload["spec_id"],
        "version": version,
        "status": "PUBLISHED",
        "published_at": utc_now(),
        "content_hash": "",
        "project": change_spec.payload["project"],
        "intent": change_spec.payload["intent"],
        "scope": change_spec.payload["scope"],
        "requirements": change_spec.payload["requirements"],
        "acceptance_criteria": change_spec.payload["acceptance_criteria"],
        "constraints": merge_lists(change_spec.payload.get("constraints", []), product_spec.payload.get("constraints", []) if product_spec else []),
        "dependencies": change_spec.payload.get("dependencies", []),
        "product_shape": merge_dicts(product_spec.payload if product_spec else {}, change_spec.payload.get("product_shape", {})),
        "source_refs": merge_lists(change_spec.payload.get("source_refs", []), [relative_uri(Path.cwd(), change_spec.path)]),
    }
    if change_spec.payload.get("approval"):
        payload["approval"] = change_spec.payload["approval"]
    payload["content_hash"] = calculate_content_hash(payload)
    return payload


def publish_change_spec(root: Path, change_spec_path: Path, version: int = 1, product_spec_path: Path | None = None) -> PublishResult:
    change_spec = load_change_spec(change_spec_path)
    issues = list(change_spec.issues)
    issues.extend(validate_revision_publish_approval(change_spec.payload, change_spec_path).issues)
    product_spec = None
    if product_spec_path is not None:
        product_spec = load_product_spec(product_spec_path)
        issues.extend(product_spec.issues)
        if product_spec.payload.get("project") != change_spec.payload.get("project"):
            issues.append(
                ValidationIssue(
                    code="publish_gate.project_mismatch",
                    severity="critical",
                    message="ProductSpec project must match ChangeSpec project",
                    path=str(product_spec_path),
                )
            )
    if issues:
        return PublishResult(path=None, payload={}, issues=issues)

    snapshot_payload = build_snapshot_payload(change_spec, version, product_spec)
    target = (
        root
        / "specs"
        / snapshot_payload["project"]
        / "published"
        / snapshot_payload["spec_id"]
        / f"v{version}"
        / "spec-snapshot.json"
    )
    if target.exists():
        return PublishResult(
            path=None,
            payload=snapshot_payload,
            issues=[
                ValidationIssue(
                    code="publish_gate.snapshot_exists",
                    severity="critical",
                    message="published SpecSnapshot already exists and cannot be overwritten",
                    path=str(target),
                )
            ],
        )

    target.parent.mkdir(parents=True, exist_ok=False)
    write_json(target, snapshot_payload)
    snapshot = load_spec_snapshot(target)
    if snapshot.issues:
        return PublishResult(path=target, payload=snapshot_payload, issues=snapshot.issues)
    return PublishResult(path=target, payload=snapshot_payload, issues=[])


def approve_revision_draft(
    root: Path,
    change_spec_path: Path,
    approved_by: str,
    reason: str = "",
    status: str = "APPROVED",
) -> ApprovalResult:
    change_spec = load_change_spec(change_spec_path)
    issues = list(change_spec.issues)
    if change_spec.payload and not is_revision_change_spec(change_spec.payload):
        issues.append(
            ValidationIssue(
                code="revision_approval.not_revision",
                severity="critical",
                message="only revision ChangeSpec drafts require this approval flow",
                path=str(change_spec_path),
            )
        )
    if status not in APPROVAL_STATUSES:
        issues.append(
            ValidationIssue(
                code="revision_approval.invalid_status",
                severity="critical",
                message=f"invalid revision approval status: {status}",
                path=str(change_spec_path),
            )
        )
    if not approved_by.strip():
        issues.append(
            ValidationIssue(
                code="revision_approval.missing_approver",
                severity="critical",
                message="approved_by is required",
                path=str(change_spec_path),
            )
        )
    if issues:
        return ApprovalResult(path=None, payload=change_spec.payload, issues=issues)

    payload = dict(change_spec.payload)
    approval = {
        "status": status,
        "approved_by": approved_by,
        "approved_at": utc_now(),
        "reason": reason,
    }
    base_version = payload.get("intent", {}).get("base_version")
    if base_version is not None:
        approval["base_version"] = base_version
    payload["approval"] = approval
    write_json(change_spec_path, payload)
    return ApprovalResult(path=change_spec_path, payload=payload, issues=[])


def validate_revision_publish_approval(payload: dict[str, Any], path: Path | None = None) -> ValidationResult:
    result = ValidationResult(ok=True)
    if not is_revision_change_spec(payload):
        return result
    approval = payload.get("approval")
    location = str(path) if path else None
    if not isinstance(approval, dict):
        result.add(
            "publish_gate.revision_not_approved",
            "critical",
            "revision ChangeSpec must be approved before publish",
            location,
        )
        return result
    if approval.get("status") != "APPROVED":
        result.add(
            "publish_gate.revision_not_approved",
            "critical",
            "revision ChangeSpec approval status must be APPROVED before publish",
            location,
        )
    if not approval.get("approved_by"):
        result.add(
            "publish_gate.missing_approver",
            "critical",
            "revision ChangeSpec approval must include approved_by",
            location,
        )
    return result


def is_revision_change_spec(payload: dict[str, Any]) -> bool:
    intent = payload.get("intent", {}) if isinstance(payload.get("intent"), dict) else {}
    if intent.get("base_version") is not None or intent.get("change_request_type") is not None:
        return True
    assumptions = payload.get("assumptions", []) if isinstance(payload.get("assumptions"), list) else []
    for assumption in assumptions:
        if isinstance(assumption, dict) and assumption.get("source") == "spec-change-request":
            return True
    return any(isinstance(item, dict) and item.get("revision_requested") for item in payload.get("requirements", []) or []) or any(
        isinstance(item, dict) and item.get("revision_requested") for item in payload.get("acceptance_criteria", []) or []
    )


def create_revision_draft_from_scr(root: Path, scr_path: Path, snapshot_path: Path) -> RevisionDraftResult:
    scr = read_json(scr_path)
    scr_result = validate_scr_like_payload(scr, scr_path)
    snapshot = load_spec_snapshot(snapshot_path)
    issues = list(scr_result.issues)
    issues.extend(snapshot.issues)
    if snapshot.issues:
        return RevisionDraftResult(path=None, payload={}, issues=issues)
    if scr.get("spec_id") != snapshot.spec_id or scr.get("base_version") != snapshot.version:
        issues.append(
            ValidationIssue(
                code="revision_draft.base_mismatch",
                severity="critical",
                message="SpecChangeRequest must match base SpecSnapshot",
                path=str(scr_path),
            )
        )
    if issues:
        return RevisionDraftResult(path=None, payload={}, issues=issues)

    payload = {
        "schema_version": snapshot.payload.get("schema_version", "1.0"),
        "spec_id": snapshot.spec_id,
        "project": snapshot.project,
        "intent": merge_dicts(
            snapshot.payload.get("intent", {}),
            {
                "revision_reason": scr.get("reason"),
                "base_version": snapshot.version,
                "change_request_type": scr.get("type"),
            },
        ),
        "scope": snapshot.payload.get("scope", {}),
        "requirements": mark_affected(
            snapshot.payload.get("requirements", []),
            set(scr.get("affected_requirements", []) or []),
            "revision_requested",
        ),
        "acceptance_criteria": mark_affected(
            snapshot.payload.get("acceptance_criteria", []),
            set(scr.get("affected_acceptance_criteria", []) or []),
            "revision_requested",
        ),
        "constraints": snapshot.payload.get("constraints", []),
        "dependencies": snapshot.payload.get("dependencies", []),
        "assumptions": [
            {
                "source": "spec-change-request",
                "run_id": scr.get("run_id"),
                "reason": scr.get("reason"),
                "affected_nodes": scr.get("affected_nodes", []),
            }
        ],
        "open_questions": [],
        "product_shape": snapshot.payload.get("product_shape", {}),
        "source_refs": merge_lists(
            snapshot.payload.get("source_refs", []),
            [relative_uri(root, snapshot_path), relative_uri(root, scr_path)],
        ),
    }
    result = validate_change_spec_payload(payload)
    if not result.ok:
        return RevisionDraftResult(path=None, payload=payload, issues=result.issues)

    target = root / "specs" / snapshot.project / "drafts" / f"{snapshot.spec_id}-v{snapshot.version + 1}-from-{scr.get('run_id')}-change-spec.json"
    if target.exists():
        return RevisionDraftResult(
            path=None,
            payload=payload,
            issues=[
                ValidationIssue(
                    code="revision_draft.exists",
                    severity="critical",
                    message="revision ChangeSpec draft already exists",
                    path=str(target),
                )
            ],
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, payload)
    return RevisionDraftResult(path=target, payload=payload, issues=[])


def validate_scr_like_payload(payload: dict[str, Any], path: Path | None = None) -> ValidationResult:
    result = ValidationResult(ok=True)
    location = str(path) if path else None
    required = ["run_id", "spec_id", "base_version", "type", "reason", "affected_requirements", "affected_acceptance_criteria", "affected_nodes"]
    for field_name in required:
        if payload.get(field_name) in (None, ""):
            result.add("revision_draft.invalid_scr", "error", f"missing SpecChangeRequest field: {field_name}", location)
    return result


def merge_lists(primary: list[Any], secondary: list[Any]) -> list[Any]:
    merged = []
    for item in primary + secondary:
        if item not in merged:
            merged.append(item)
    return merged


def merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update(override)
    return merged


def mark_affected(items: list[Any], affected_ids: set[str], marker: str) -> list[Any]:
    marked = []
    for item in items:
        if not isinstance(item, dict):
            marked.append(item)
            continue
        copied = dict(item)
        if str(copied.get("id")) in affected_ids:
            copied[marker] = True
        marked.append(copied)
    return marked


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
