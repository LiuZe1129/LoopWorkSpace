"""Design Spec — the design contract between Requirement and ExecutionPlan.

P0: minimal closed loop. ``design_spec_node`` aggregates architecture +
snapshot into a ``design-spec.json``; ``design_gate_node`` validates
traceability, open_questions, and source_snapshot hash binding.

Deterministic fallback generates a minimal ``design-spec.json`` so existing
tests (stub architect) stay green — same opt-in philosophy as
``intent_audit`` NO_INTENT_DOCS and ``huaDesign`` design_system gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runs import GraphRunStore, read_json, relative_to_root, utc_now, write_json
from .specs import load_spec_snapshot
from .state import ValidationIssue, ValidationResult


DESIGN_SPEC_SCHEMA_VERSIONS = {"1.0"}

DESIGN_SPEC_REQUIRED_FIELDS = [
    "schema_version",
    "project",
    "spec_id",
    "spec_version",
    "design_id",
    "status",
    "source_snapshot",
    "architecture",
    "traceability",
    "open_questions",
    "approval",
]


@dataclass(frozen=True)
class DesignSpec:
    path: Path | None
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class DesignGateCheck:
    """A single design gate check result."""
    id: str
    name: str
    passed: bool
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
        }


def generate_minimal_design_spec(root: Path, run_id: str) -> DesignSpec:
    """Generate a minimal ``design-spec.json`` from snapshot + architecture artifact.

    This is the deterministic fallback used when no LLM architect agent is
    registered.  It produces a valid but minimal design-spec.json that
    satisfies the design gate checks, ensuring the DRG chain stays green in
    test runtime.
    """
    store = GraphRunStore(root)
    state = store.read_state(run_id)
    snapshot = load_spec_snapshot(root / state.bound_spec.snapshot_uri)
    payload = snapshot.payload

    requirements = payload.get("requirements", []) or []
    acceptance_criteria = payload.get("acceptance_criteria", []) or []

    # Try to read architecture artifact for richer data
    arch_path = store.run_dir(run_id) / "artifacts" / "architecture_impact_node.json"
    arch_data = read_json(arch_path) if arch_path.is_file() else {}
    arch_block: dict[str, Any] = {}
    if isinstance(arch_data, dict):
        arch_block = arch_data.get("architecture", {}) if isinstance(arch_data.get("architecture"), dict) else {}

    # Build component boundaries from requirements (minimal)
    component_boundaries: list[dict[str, Any]] = []
    for req in requirements:
        if not isinstance(req, dict):
            continue
        req_id = str(req.get("id", ""))
        if not req_id:
            continue
        component_boundaries.append({
            "name": f"Component-{req_id}",
            "type": "module",
            "responsibilities": [str(req.get("system_response", req.get("summary", "")))],
            "requirements": [req_id],
        })

    # Build traceability: each requirement -> design_refs + acceptance_ids
    traceability: list[dict[str, Any]] = []
    for req in requirements:
        if not isinstance(req, dict):
            continue
        req_id = str(req.get("id", ""))
        if not req_id:
            continue
        design_refs = [
            cb["name"]
            for cb in component_boundaries
            if req_id in cb.get("requirements", [])
        ]
        acc_ids: list[str] = []
        for acc in acceptance_criteria:
            if not isinstance(acc, dict):
                continue
            acc_id = str(acc.get("id", ""))
            if not acc_id:
                continue
            req_refs = acc.get("requirement_ids", [])
            if isinstance(req_refs, list) and req_id in req_refs:
                acc_ids.append(acc_id)
            elif str(acc.get("requirement_id", "")) == req_id:
                acc_ids.append(acc_id)
        traceability.append({
            "requirement_id": req_id,
            "design_refs": design_refs,
            "acceptance_ids": acc_ids,
        })

    # Build test_strategy from acceptance (minimal)
    test_strategy: list[dict[str, Any]] = []
    for acc in acceptance_criteria:
        if not isinstance(acc, dict):
            continue
        acc_id = str(acc.get("id", ""))
        if not acc_id:
            continue
        ev_req = acc.get("evidence_required", ["test-result"])
        if not isinstance(ev_req, list) or not ev_req:
            ev_req = ["test-result"]
        test_strategy.append({
            "acceptance_id": acc_id,
            "test_level": "component",
            "approach": str(acc.get("scenario", acc.get("summary", "verify acceptance"))),
            "evidence_required": list(ev_req),
        })

    # Build api_contracts / data_model / ui_contract from architecture (minimal)
    api_contracts: list[dict[str, Any]] = []
    data_model: list[dict[str, Any]] = []
    ui_contract: dict[str, Any] = {}

    design_id = f"design-{state.bound_spec.spec_id}-v{state.bound_spec.version}"
    tech_stack = arch_block.get("tech_stack") if isinstance(arch_block.get("tech_stack"), dict) else {}
    affected_modules = list(tech_stack.keys()) if tech_stack else []

    spec_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "project": snapshot.project,
        "spec_id": state.bound_spec.spec_id,
        "spec_version": state.bound_spec.version,
        "design_id": design_id,
        "status": "DRAFT",
        "source_snapshot": {
            "spec_id": state.bound_spec.spec_id,
            "version": state.bound_spec.version,
            "content_hash": state.bound_spec.content_hash,
        },
        "architecture": {
            "summary": str(
                arch_block.get("directory_structure")
                or "auto-generated minimal design spec (deterministic fallback)"
            ),
            "affected_modules": affected_modules,
            "component_boundaries": component_boundaries,
        },
        "api_contracts": api_contracts,
        "data_model": data_model,
        "ui_contract": ui_contract,
        "state_transitions": [],
        "error_handling": [],
        "test_strategy": test_strategy,
        "traceability": traceability,
        "open_questions": [],
        "risks": [],
        "approval": {
            "status": "PENDING",
            "approved_by": None,
            "approved_at": None,
            "content_hash": None,
        },
    }
    return DesignSpec(path=None, payload=spec_payload, issues=[])


def write_design_spec(root: Path, run_id: str, payload: dict[str, Any] | None = None) -> Path:
    """Write ``design-spec.json`` to run directory."""
    store = GraphRunStore(root)
    path = store.run_dir(run_id) / "design-spec.json"
    if payload is None:
        spec = generate_minimal_design_spec(root, run_id)
        payload = spec.payload
    write_json(path, payload)
    return path


def load_design_spec(path: Path) -> DesignSpec:
    payload = read_json(path)
    result = validate_design_spec_payload(payload, path)
    return DesignSpec(path=path, payload=payload, issues=result.issues)


def validate_design_spec_payload(payload: dict[str, Any], path: Path | None = None) -> ValidationResult:
    result = ValidationResult(ok=True)
    location = str(path) if path else None

    for field_name in DESIGN_SPEC_REQUIRED_FIELDS:
        if payload.get(field_name) in (None, ""):
            result.add(
                "design_spec.missing_field",
                "error",
                f"missing design spec field: {field_name}",
                location,
            )

    if str(payload.get("schema_version", "")) not in DESIGN_SPEC_SCHEMA_VERSIONS:
        result.add(
            "design_spec.unsupported_schema",
            "error",
            f"unsupported design spec schema_version: {payload.get('schema_version')}",
            location,
        )

    arch = payload.get("architecture")
    if not isinstance(arch, dict):
        result.add("design_spec.invalid_architecture", "error", "architecture must be an object", location)

    source = payload.get("source_snapshot")
    if not isinstance(source, dict):
        result.add("design_spec.invalid_source_snapshot", "error", "source_snapshot must be an object", location)

    approval = payload.get("approval")
    if not isinstance(approval, dict):
        result.add("design_spec.invalid_approval", "error", "approval must be an object", location)

    for field_name in ["traceability", "open_questions", "test_strategy", "risks",
                       "api_contracts", "data_model", "state_transitions", "error_handling"]:
        if field_name in payload and not isinstance(payload[field_name], list):
            result.add("design_spec.invalid_list", "error", f"{field_name} must be a list", location)

    return result


def run_design_gate_checks(root: Path, run_id: str) -> list[DesignGateCheck]:
    """Run design gate checks.

    Returns an empty list (PASS) when no ``design-spec.json`` exists — the
    opt-in philosophy: no design spec means the gate auto-passes, same as
    ``intent_audit`` NO_INTENT_DOCS.
    """
    store = GraphRunStore(root)
    spec_path = store.run_dir(run_id) / "design-spec.json"
    if not spec_path.is_file():
        return []

    spec = load_design_spec(spec_path)
    if spec.issues:
        return [
            DesignGateCheck(
                id="design_spec.valid",
                name="Design Spec valid",
                passed=False,
                message=f"design-spec.json has validation issues: {'; '.join(i.message for i in spec.issues)}",
            )
        ]

    payload = spec.payload
    checks: list[DesignGateCheck] = []

    # Check 1: open_questions must be empty
    open_qs = payload.get("open_questions", []) or []
    checks.append(DesignGateCheck(
        id="design_gate.open_questions_resolved",
        name="Open questions resolved",
        passed=len(open_qs) == 0,
        message=f"open_questions has {len(open_qs)} unresolved items" if open_qs else "",
    ))

    # Check 2: each requirement in traceability has at least one design_ref
    traceability = payload.get("traceability", []) or []
    no_design_refs = [
        t for t in traceability
        if isinstance(t, dict) and not t.get("design_refs")
    ]
    checks.append(DesignGateCheck(
        id="design_gate.requirement_has_design_refs",
        name="Every requirement has design refs",
        passed=len(no_design_refs) == 0,
        message=(
            f"requirements without design_refs: {[t.get('requirement_id') for t in no_design_refs]}"
            if no_design_refs else ""
        ),
    ))

    # Check 3: source_snapshot.content_hash matches bound spec
    state = store.read_state(run_id)
    source_hash = str(payload.get("source_snapshot", {}).get("content_hash", ""))
    bound_hash = state.bound_spec.content_hash
    hash_match = (not source_hash) or (not bound_hash) or (source_hash == bound_hash)
    checks.append(DesignGateCheck(
        id="design_gate.source_hash_matches",
        name="Source snapshot hash matches bound spec",
        passed=hash_match,
        message=(
            f"hash mismatch: design-spec={source_hash}, bound={bound_hash}"
            if not hash_match else ""
        ),
    ))

    # Check 4: risks with launch_blocking urgency must have mitigation
    risks = payload.get("risks", []) or []
    unmitigated = [
        r for r in risks
        if isinstance(r, dict)
        and r.get("urgency") == "launch_blocking"
        and not r.get("mitigation")
    ]
    checks.append(DesignGateCheck(
        id="design_gate.launch_blocking_risks_mitigated",
        name="Launch-blocking risks have mitigation",
        passed=len(unmitigated) == 0,
        message=f"unmitigated launch-blocking risks: {len(unmitigated)}" if unmitigated else "",
    ))

    return checks


def serialize_design_gate_checks(checks: list[DesignGateCheck]) -> list[dict[str, Any]]:
    return [check.to_dict() for check in checks]
