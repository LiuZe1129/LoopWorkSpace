from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .state import ValidationIssue, ValidationResult


SUPPORTED_SPEC_SCHEMA_VERSIONS = {"1.0", "1.1"}
PUBLISHED_STATUS = "PUBLISHED"
SNAPSHOT_REQUIRED_FIELDS = [
    "schema_version",
    "spec_id",
    "version",
    "status",
    "published_at",
    "content_hash",
    "project",
    "intent",
    "scope",
    "requirements",
    "acceptance_criteria",
    "constraints",
    "dependencies",
    "product_shape",
    "source_refs",
]

REQUIREMENT_TYPES = {"functional", "non_functional", "constraint", "ui", "data", "security"}
REQUIREMENT_PRIORITIES = {"MUST", "SHOULD", "MAY"}
ACCEPTANCE_TYPES = {"automated", "manual", "review", "performance", "security"}
STRICT_SCHEMA_VERSION = "1.1"


@dataclass(frozen=True)
class SpecRef:
    spec_id: str
    version: int
    content_hash: str
    snapshot_uri: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "content_hash": self.content_hash,
            "snapshot_uri": self.snapshot_uri,
        }


@dataclass(frozen=True)
class SpecSnapshot:
    path: Path
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def spec_id(self) -> str:
        return str(self.payload.get("spec_id", ""))

    @property
    def version(self) -> int:
        return int(self.payload.get("version", 0) or 0)

    @property
    def project(self) -> str:
        return str(self.payload.get("project", ""))

    @property
    def content_hash(self) -> str:
        return str(self.payload.get("content_hash", ""))

    @property
    def status(self) -> str:
        return str(self.payload.get("status", ""))

    def spec_ref(self, root: Path) -> SpecRef:
        return SpecRef(
            spec_id=self.spec_id,
            version=self.version,
            content_hash=self.content_hash,
            snapshot_uri=relative_uri(root, self.path),
        )


def relative_uri(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def canonical_spec_bytes(payload: dict[str, Any]) -> bytes:
    canonical = dict(payload)
    canonical.pop("content_hash", None)
    return json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def calculate_content_hash(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_spec_bytes(payload)).hexdigest()


def load_spec_snapshot(path: Path) -> SpecSnapshot:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = validate_spec_snapshot_payload(payload, path)
    return SpecSnapshot(path=path, payload=payload, issues=result.issues)


def validate_strict_requirement_acceptance(payload: dict[str, Any], path: Path | None = None) -> ValidationResult:
    """1.1 严格校验：Requirement EARS + Acceptance GWT + 显式映射 + 类型条件。

    仅当 schema_version == "1.1" 时调用。1.0 向后兼容不强制。
    """
    result = ValidationResult(ok=True)
    location = str(path) if path else None
    requirements = payload.get("requirements", []) or []
    acceptance_criteria = payload.get("acceptance_criteria", []) or []

    # --- Requirement 校验：EARS 结构 + 唯一 ID ---
    req_ids: set[str] = set()
    for req in requirements:
        if not isinstance(req, dict):
            result.add("requirement.invalid", "error", "requirement must be an object", location)
            continue
        req_id = str(req.get("id", ""))
        if not req_id:
            result.add("requirement.missing_id", "error", "requirement must have an id", location)
            continue
        if req_id in req_ids:
            result.add("requirement.duplicate_id", "critical", f"duplicate requirement id: {req_id}", location)
            continue
        req_ids.add(req_id)

        req_type = req.get("type")
        if not req_type:
            result.add("requirement.missing_type", "error", f"requirement {req_id} must have type", location)
        elif req_type not in REQUIREMENT_TYPES:
            result.add("requirement.invalid_type", "error", f"requirement {req_id} invalid type: {req_type}", location)

        priority = req.get("priority")
        if not priority:
            result.add("requirement.missing_priority", "error", f"requirement {req_id} must have priority", location)
        elif priority not in REQUIREMENT_PRIORITIES:
            result.add("requirement.invalid_priority", "error", f"requirement {req_id} invalid priority: {priority}", location)

        for ears_field in ("actor", "condition", "system_response"):
            val = req.get(ears_field)
            if not val or not str(val).strip():
                result.add(
                    "requirement.missing_ears",
                    "error",
                    f"requirement {req_id} must have non-empty {ears_field}",
                    location,
                )

    # --- Acceptance 校验：GWT 结构 + 类型条件 + requirement_ids 引用 ---
    acc_ids: set[str] = set()
    acc_to_reqs: dict[str, list[str]] = {}
    for acc in acceptance_criteria:
        if not isinstance(acc, dict):
            result.add("acceptance.invalid", "error", "acceptance must be an object", location)
            continue
        acc_id = str(acc.get("id", ""))
        if not acc_id:
            result.add("acceptance.missing_id", "error", "acceptance must have an id", location)
            continue
        if acc_id in acc_ids:
            result.add("acceptance.duplicate_id", "critical", f"duplicate acceptance id: {acc_id}", location)
            continue
        acc_ids.add(acc_id)

        req_ids_ref = acc.get("requirement_ids")
        if not req_ids_ref or not isinstance(req_ids_ref, list):
            result.add(
                "acceptance.missing_requirement_ids",
                "critical",
                f"acceptance {acc_id} must have non-empty requirement_ids",
                location,
            )
        else:
            acc_to_reqs[acc_id] = [str(r) for r in req_ids_ref]
            for ref_id in req_ids_ref:
                if str(ref_id) not in req_ids:
                    result.add(
                        "acceptance.invalid_requirement_ref",
                        "critical",
                        f"acceptance {acc_id} references unknown requirement: {ref_id}",
                        location,
                    )

        acc_type = acc.get("type")
        if not acc_type:
            result.add("acceptance.missing_type", "error", f"acceptance {acc_id} must have type", location)
        elif acc_type not in ACCEPTANCE_TYPES:
            result.add("acceptance.invalid_type", "error", f"acceptance {acc_id} invalid type: {acc_type}", location)
        else:
            _validate_acceptance_type_conditions(acc_id, acc_type, acc, result, location)

        for gwt_field in ("given", "when", "then"):
            val = acc.get(gwt_field)
            if not val or not isinstance(val, list) or not val:
                result.add(
                    "acceptance.missing_gwt",
                    "error",
                    f"acceptance {acc_id} must have non-empty {gwt_field} list",
                    location,
                )

        ev_req = acc.get("evidence_required")
        if not ev_req or not isinstance(ev_req, list) or not ev_req:
            result.add(
                "acceptance.missing_evidence_required",
                "error",
                f"acceptance {acc_id} must have non-empty evidence_required",
                location,
            )

    # --- 映射完整性：每个 Requirement 至少被一个 Acceptance 覆盖 ---
    covered_reqs: set[str] = set()
    for refs in acc_to_reqs.values():
        covered_reqs.update(refs)
    for orphan in sorted(req_ids - covered_reqs):
        result.add(
            "requirement.orphan_no_acceptance",
            "critical",
            f"requirement {orphan} has no acceptance coverage",
            location,
        )

    return result


def _validate_acceptance_type_conditions(
    acc_id: str,
    acc_type: str,
    acc: dict[str, Any],
    result: ValidationResult,
    location: str | None,
) -> None:
    """按 acceptance.type 校验条件必需字段。"""
    if acc_type == "automated":
        if not str(acc.get("check", "")).strip():
            result.add("acceptance.automated_missing_check", "critical", f"automated acceptance {acc_id} must have check", location)
        if not str(acc.get("expect", "")).strip():
            result.add("acceptance.automated_missing_expect", "critical", f"automated acceptance {acc_id} must have expect", location)
    elif acc_type == "manual":
        if not acc.get("evidence_required"):
            result.add("acceptance.manual_missing_evidence", "critical", f"manual acceptance {acc_id} must have evidence_required", location)
        if not acc.get("reviewer") and not acc.get("review_scope"):
            result.add("acceptance.manual_missing_reviewer", "critical", f"manual acceptance {acc_id} must have reviewer or review_scope", location)
    elif acc_type == "review":
        if not acc.get("review_scope"):
            result.add("acceptance.review_missing_scope", "critical", f"review acceptance {acc_id} must have review_scope", location)
    elif acc_type == "performance":
        if not acc.get("metric"):
            result.add("acceptance.performance_missing_metric", "critical", f"performance acceptance {acc_id} must have metric", location)
        if not acc.get("threshold"):
            result.add("acceptance.performance_missing_threshold", "critical", f"performance acceptance {acc_id} must have threshold", location)
    elif acc_type == "security":
        has_threat_control = acc.get("threat") and acc.get("control")
        has_check = acc.get("check")
        has_scope = acc.get("review_scope")
        if not (has_threat_control or has_check or has_scope):
            result.add("acceptance.security_missing_evidence", "critical", f"security acceptance {acc_id} must have threat+control or check or review_scope", location)


def validate_spec_snapshot_payload(payload: dict[str, Any], path: Path | None = None) -> ValidationResult:
    result = ValidationResult(ok=True)
    location = str(path) if path else None

    for field_name in SNAPSHOT_REQUIRED_FIELDS:
        if payload.get(field_name) in (None, ""):
            result.add("spec_snapshot.missing_field", "error", f"missing snapshot field: {field_name}", location)

    if payload.get("schema_version") not in SUPPORTED_SPEC_SCHEMA_VERSIONS:
        result.add(
            "spec_snapshot.unsupported_schema",
            "error",
            f"unsupported spec snapshot schema_version: {payload.get('schema_version')}",
            location,
        )

    if payload.get("schema_version") == STRICT_SCHEMA_VERSION:
        result.issues.extend(validate_strict_requirement_acceptance(payload, path).issues)

    if payload.get("status") != PUBLISHED_STATUS:
        result.add(
            "spec_snapshot.not_published",
            "critical",
            "GraphFlow can only bind status=PUBLISHED snapshots",
            location,
        )

    if not isinstance(payload.get("version"), int) or payload.get("version", 0) < 1:
        result.add("spec_snapshot.invalid_version", "error", "snapshot version must be a positive integer", location)

    for field_name in ["requirements", "acceptance_criteria", "constraints", "dependencies", "source_refs"]:
        if field_name in payload and not isinstance(payload[field_name], list):
            result.add("spec_snapshot.invalid_list", "error", f"{field_name} must be a list", location)

    for field_name in ["intent", "scope", "product_shape"]:
        if field_name in payload and not isinstance(payload[field_name], dict):
            result.add("spec_snapshot.invalid_object", "error", f"{field_name} must be an object", location)

    expected_hash = calculate_content_hash(payload)
    actual_hash = payload.get("content_hash")
    if actual_hash and actual_hash != expected_hash:
        result.add(
            "spec_snapshot.hash_mismatch",
            "critical",
            f"content_hash mismatch: expected {expected_hash}, got {actual_hash}",
            location,
        )

    return result
