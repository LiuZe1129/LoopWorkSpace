from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import EventLogger
from .runs import GraphRunStore, read_json, write_json
from .specs import SpecSnapshot, load_spec_snapshot
from .state import ValidationResult


SUPPORTED_TEMPLATES = {"web-feature-v1", "qt-feature-v1"}
PLAN_REQUIRED_FIELDS = [
    "schema_version",
    "run_id",
    "spec_id",
    "spec_version",
    "template_id",
    "work_packages",
    "depends_on",
    "agent_hints",
    "requirement_coverage",
    "acceptance_coverage",
    "risk_tags",
    "environment_requirements",
]


@dataclass(frozen=True)
class ExecutionPlan:
    path: Path | None
    payload: dict[str, Any]
    issues: list = field(default_factory=list)

    @property
    def template_id(self) -> str:
        return str(self.payload.get("template_id", ""))

    @property
    def work_packages(self) -> list[dict[str, Any]]:
        return list(self.payload.get("work_packages", []) or [])


def build_execution_plan(run_id: str, snapshot: SpecSnapshot) -> ExecutionPlan:
    template_id = select_template(snapshot)
    is_strict = str(snapshot.payload.get("schema_version", "1.0")) == "1.1"
    requirement_ids = extract_ids(snapshot.payload.get("requirements", []), prefix="req")
    acceptance_ids = extract_ids(snapshot.payload.get("acceptance_criteria", []), prefix="acc")
    acceptance_items = snapshot.payload.get("acceptance_criteria", []) or []
    requirements_data = {
        str(r.get("id", "")): r
        for r in (snapshot.payload.get("requirements", []) or [])
        if isinstance(r, dict)
    }
    acc_by_requirement: dict[str, list[str]] = {}
    unassigned_acc_ids: list[str] = []
    for idx, acc_item in enumerate(acceptance_items):
        acc_id = acceptance_ids[idx] if idx < len(acceptance_ids) else f"acc-{idx + 1}"
        if not isinstance(acc_item, dict):
            unassigned_acc_ids.append(acc_id)
            continue
        if is_strict:
            for ref_id in acc_item.get("requirement_ids", []) or []:
                acc_by_requirement.setdefault(str(ref_id), []).append(acc_id)
        elif acc_item.get("requirement_id"):
            req_id = str(acc_item["requirement_id"])
            acc_by_requirement.setdefault(req_id, []).append(acc_id)
        else:
            unassigned_acc_ids.append(acc_id)
    work_packages = []

    if requirement_ids:
        for index, requirement_id in enumerate(requirement_ids, start=1):
            wp_acc_ids = list(acc_by_requirement.get(requirement_id, []))
            if not is_strict and index == 1 and unassigned_acc_ids:
                wp_acc_ids.extend(unassigned_acc_ids)
            wp: dict[str, Any] = {
                "id": f"wp-{slug(requirement_id)}",
                "title": f"Implement {requirement_id}",
                "node_type": "implementation",
                "agent": implementation_agent(template_id),
                "requirements": [requirement_id],
                "acceptance_criteria": wp_acc_ids,
            }
            if is_strict:
                req_data = requirements_data.get(requirement_id, {})
                wp["requirement_type"] = str(req_data.get("type", "functional"))
                wp["priority"] = str(req_data.get("priority", "MUST"))
                wp["tags"] = list(req_data.get("tags", []) or [])
                wp["implementation_focus"] = str(req_data.get("system_response", ""))
                wp["planning_basis"] = "requirement_ears"
            work_packages.append(wp)
    else:
        work_packages.append(
            {
                "id": "wp-main",
                "title": "Implement requested change",
                "node_type": "implementation",
                "agent": implementation_agent(template_id),
                "requirements": [],
                "acceptance_criteria": acceptance_ids,
            }
        )

    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "spec_id": snapshot.spec_id,
        "spec_version": snapshot.version,
        "template_id": template_id,
        "work_packages": work_packages,
        "depends_on": [],
        "agent_hints": [{"work_package_id": wp["id"], "agent": wp["agent"]} for wp in work_packages],
        "requirement_coverage": [
            {"requirement_id": requirement_id, "work_package_id": f"wp-{slug(requirement_id)}"}
            for requirement_id in requirement_ids
        ],
        "acceptance_coverage": [
            {"acceptance_id": acc_id, "work_package_id": wp["id"], "verification": "test"}
            for wp in work_packages
            for acc_id in wp.get("acceptance_criteria", [])
        ],
        "risk_tags": list(snapshot.payload.get("risk_flags", []) or []),
        "environment_requirements": list(snapshot.payload.get("dependencies", []) or []),
    }
    result = validate_execution_plan_payload(payload, snapshot)
    return ExecutionPlan(path=None, payload=payload, issues=result.issues)


def generate_execution_plan(root: Path, run_id: str) -> ExecutionPlan:
    store = GraphRunStore(root)
    state = store.read_state(run_id)
    snapshot = load_spec_snapshot(root / state.bound_spec.snapshot_uri)
    plan = build_execution_plan(run_id, snapshot)
    if plan.issues:
        return plan
    path = store.run_dir(run_id) / "execution-plan.json"
    write_json(path, plan.payload)
    EventLogger(root).append_run(run_id, "plan_generated", {"run_id": run_id, "template_id": plan.template_id})
    return ExecutionPlan(path=path, payload=plan.payload, issues=[])


def load_execution_plan(path: Path, snapshot: SpecSnapshot | None = None) -> ExecutionPlan:
    payload = read_json(path)
    result = validate_execution_plan_payload(payload, snapshot)
    return ExecutionPlan(path=path, payload=payload, issues=result.issues)


def validate_execution_plan_payload(payload: dict[str, Any], snapshot: SpecSnapshot | None = None) -> ValidationResult:
    result = ValidationResult(ok=True)
    for field_name in PLAN_REQUIRED_FIELDS:
        if payload.get(field_name) in (None, ""):
            result.add("execution_plan.missing_field", "error", f"missing execution plan field: {field_name}")

    if payload.get("template_id") not in SUPPORTED_TEMPLATES:
        result.add("execution_plan.unsupported_template", "error", f"unsupported template_id: {payload.get('template_id')}")

    for field_name in ["work_packages", "depends_on", "agent_hints", "requirement_coverage", "acceptance_coverage"]:
        if field_name in payload and not isinstance(payload[field_name], list):
            result.add("execution_plan.invalid_list", "error", f"{field_name} must be a list")

    work_package_ids = {wp.get("id") for wp in payload.get("work_packages", []) if isinstance(wp, dict)}
    for work_package in payload.get("work_packages", []) or []:
        if not isinstance(work_package, dict):
            result.add("execution_plan.invalid_work_package", "error", "work package must be an object")
            continue
        for field_name in ["id", "title", "node_type", "agent", "requirements", "acceptance_criteria"]:
            if work_package.get(field_name) in (None, ""):
                result.add("execution_plan.invalid_work_package", "error", f"work package missing field: {field_name}")

    for dependency in payload.get("depends_on", []) or []:
        source = dependency.get("source")
        target = dependency.get("target")
        if source not in work_package_ids or target not in work_package_ids:
            result.add("execution_plan.invalid_dependency", "error", "depends_on references unknown work package")

    for coverage in payload.get("requirement_coverage", []) or []:
        if coverage.get("work_package_id") not in work_package_ids:
            result.add("execution_plan.invalid_requirement_coverage", "error", "requirement coverage references unknown work package")

    for coverage in payload.get("acceptance_coverage", []) or []:
        if coverage.get("work_package_id") not in work_package_ids:
            result.add("execution_plan.invalid_acceptance_coverage", "error", "acceptance coverage references unknown work package")

    if snapshot is not None:
        requirement_ids = set(extract_ids(snapshot.payload.get("requirements", []), prefix="req"))
        acceptance_ids = set(extract_ids(snapshot.payload.get("acceptance_criteria", []), prefix="acc"))
        covered_requirements = {item.get("requirement_id") for item in payload.get("requirement_coverage", []) or []}
        covered_acceptance = {item.get("acceptance_id") for item in payload.get("acceptance_coverage", []) or []}
        missing_requirements = sorted(requirement_ids - covered_requirements)
        missing_acceptance = sorted(acceptance_ids - covered_acceptance)
        if missing_requirements:
            result.add("execution_plan.missing_requirement_coverage", "critical", f"missing requirement coverage: {missing_requirements}")
        if missing_acceptance:
            result.add("execution_plan.missing_acceptance_coverage", "critical", f"missing acceptance coverage: {missing_acceptance}")

    return result


def select_template(snapshot: SpecSnapshot) -> str:
    product_shape = snapshot.payload.get("product_shape", {}) or {}
    marker = " ".join(
        [
            snapshot.project,
            str(product_shape.get("product_type", "")),
            str(product_shape.get("platform", "")),
        ]
    ).lower()
    if "qt" in marker or "pc-client" in marker:
        return "qt-feature-v1"
    return "web-feature-v1"


def implementation_agent(template_id: str) -> str:
    return "client-dev" if template_id == "qt-feature-v1" else "frontend-dev"


def extract_ids(items: list[Any], prefix: str) -> list[str]:
    ids = []
    for index, item in enumerate(items or [], start=1):
        if isinstance(item, dict):
            ids.append(str(item.get("id") or f"{prefix}-{index}"))
        else:
            ids.append(f"{prefix}-{index}")
    return ids


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-").lower()
    return cleaned or "main"


# ---------------------------------------------------------------------------
# P1: Design-Spec-Aware plan refinement
# ---------------------------------------------------------------------------


def is_rich_design_spec(design_spec_payload: dict[str, Any]) -> bool:
    """Check if a design spec is rich enough to trigger plan refinement.

    A design spec is "rich" when it has design detail beyond the minimal
    deterministic fallback:

    - ``api_contracts`` is non-empty, OR
    - ``data_model`` is non-empty, OR
    - ``component_boundaries`` count differs from ``traceability`` count
      (the fallback always produces 1:1 boundary-per-requirement)

    The minimal deterministic fallback (empty api_contracts/data_model,
    exactly one boundary per requirement) returns ``False``, keeping
    existing stub-based tests green.
    """
    api_contracts = design_spec_payload.get("api_contracts", []) or []
    if api_contracts:
        return True
    data_model = design_spec_payload.get("data_model", []) or []
    if data_model:
        return True
    boundaries = (
        design_spec_payload.get("architecture", {}).get("component_boundaries", []) or []
    )
    traceability = design_spec_payload.get("traceability", []) or []
    if len(boundaries) != len(traceability):
        return True
    return False


def boundary_agent(boundary_type: str, template_id: str) -> str:
    """Map a component boundary type to an implementation agent."""
    bt = (boundary_type or "").lower()
    if "frontend" in bt or "ui" in bt:
        return "frontend-dev"
    if "backend" in bt or "data" in bt or "service" in bt or "api" in bt:
        return "backend-dev"
    return implementation_agent(template_id)


def refine_plan_from_design_spec(root: Path, run_id: str) -> tuple[bool, ExecutionPlan]:
    """Refine ``execution-plan.json`` from ``design-spec.json`` when rich.

    Returns ``(refined, plan)``.  When the design spec is not rich or does
    not exist, ``refined=False`` and ``plan`` is the original unchanged plan.

    WP derivation rules:

    - Each ``component_boundary`` → one implementation WP (agent from type)
    - ``api_contracts`` → one merged ``wp-api`` WP (agent=backend-dev)
    - ``data_model`` → one merged ``wp-data`` WP (agent=backend-dev)
    - Uncovered requirements → fallback WP (agent from template)

    Each refined WP is tagged ``planning_basis: "design_spec"``.  Fallback
    WPs keep ``planning_basis: "requirement_ears"``.
    """
    from .design_spec import load_design_spec

    store = GraphRunStore(root)
    run_dir = store.run_dir(run_id)
    plan_path = run_dir / "execution-plan.json"
    plan = load_execution_plan(plan_path)

    spec_path = run_dir / "design-spec.json"
    if not spec_path.is_file():
        return False, plan

    spec = load_design_spec(spec_path)
    if spec.issues:
        return False, plan

    if not is_rich_design_spec(spec.payload):
        return False, plan

    # Read snapshot for requirement/acceptance metadata
    state = store.read_state(run_id)
    snapshot = load_spec_snapshot(root / state.bound_spec.snapshot_uri)
    requirements_data: dict[str, dict[str, Any]] = {
        str(r.get("id", "")): r
        for r in (snapshot.payload.get("requirements", []) or [])
        if isinstance(r, dict)
    }
    acceptance_items = snapshot.payload.get("acceptance_criteria", []) or []

    # Build acceptance reverse lookups: requirement_id -> [acc_id]
    acc_by_requirement: dict[str, list[str]] = {}
    for idx, acc_item in enumerate(acceptance_items):
        acc_id = (
            str(acc_item.get("id", f"acc-{idx + 1}"))
            if isinstance(acc_item, dict)
            else f"acc-{idx + 1}"
        )
        if not isinstance(acc_item, dict):
            continue
        req_refs = acc_item.get("requirement_ids", [])
        if isinstance(req_refs, list):
            for ref_id in req_refs:
                acc_by_requirement.setdefault(str(ref_id), []).append(acc_id)
        elif acc_item.get("requirement_id"):
            acc_by_requirement.setdefault(str(acc_item["requirement_id"]), []).append(acc_id)

    # Traceability reverse lookup: requirement_id -> [acc_id] (preferred)
    traceability = spec.payload.get("traceability", []) or []
    trace_acc_by_req: dict[str, list[str]] = {}
    for t in traceability:
        if not isinstance(t, dict):
            continue
        req_id = str(t.get("requirement_id", ""))
        acc_ids = t.get("acceptance_ids", []) or []
        if req_id and acc_ids:
            trace_acc_by_req[req_id] = list(acc_ids)

    def _acc_for_req(req_id: str) -> list[str]:
        """Get acceptance IDs for a requirement (traceability preferred)."""
        return trace_acc_by_req.get(req_id, acc_by_requirement.get(req_id, []))

    def _enrich_wp(wp: dict[str, Any], req_ids: list[str], focus: str, requirements_data: dict[str, dict[str, Any]]) -> None:
        """Enrich WP with requirement metadata if available (1.1 fields)."""
        if not req_ids:
            return
        req_data = requirements_data.get(req_ids[0], {})
        if not req_data:
            return
        wp["requirement_type"] = str(req_data.get("type", "functional"))
        wp["priority"] = str(req_data.get("priority", "MUST"))
        wp["tags"] = list(req_data.get("tags", []) or [])
        wp["implementation_focus"] = focus or str(req_data.get("system_response", ""))

    template_id = plan.template_id
    work_packages: list[dict[str, Any]] = []
    covered_requirements: set[str] = set()

    # 1. Component boundaries → one WP each
    boundaries = spec.payload.get("architecture", {}).get("component_boundaries", []) or []
    for boundary in boundaries:
        if not isinstance(boundary, dict):
            continue
        name = str(boundary.get("name", "component"))
        boundary_type = str(boundary.get("type", "module"))
        req_ids = [str(r) for r in boundary.get("requirements", []) or []]
        covered_requirements.update(req_ids)
        acc_ids: list[str] = []
        for req_id in req_ids:
            acc_ids.extend(_acc_for_req(req_id))
        wp = {
            "id": f"wp-{slug(name)}",
            "title": f"Implement {name}",
            "node_type": "implementation",
            "agent": boundary_agent(boundary_type, template_id),
            "requirements": req_ids,
            "acceptance_criteria": list(dict.fromkeys(acc_ids)),
            "planning_basis": "design_spec",
        }
        focus = "; ".join(str(r) for r in boundary.get("responsibilities", []) or [])
        _enrich_wp(wp, req_ids, focus, requirements_data)
        work_packages.append(wp)

    # 2. API contracts → one merged WP
    api_contracts = spec.payload.get("api_contracts", []) or []
    if api_contracts:
        api_req_ids: list[str] = []
        for contract in api_contracts:
            if isinstance(contract, dict):
                api_req_ids.extend(str(r) for r in contract.get("requirements", []) or [])
        api_req_ids = list(dict.fromkeys(api_req_ids))
        covered_requirements.update(api_req_ids)
        acc_ids = []
        for req_id in api_req_ids:
            acc_ids.extend(_acc_for_req(req_id))
        wp = {
            "id": "wp-api",
            "title": "Implement API contracts",
            "node_type": "implementation",
            "agent": "backend-dev",
            "requirements": api_req_ids,
            "acceptance_criteria": list(dict.fromkeys(acc_ids)),
            "planning_basis": "design_spec",
        }
        _enrich_wp(wp, api_req_ids, "API contract implementation", requirements_data)
        work_packages.append(wp)

    # 3. Data model → one merged WP
    data_model = spec.payload.get("data_model", []) or []
    if data_model:
        data_req_ids: list[str] = []
        for model in data_model:
            if isinstance(model, dict):
                data_req_ids.extend(str(r) for r in model.get("requirements", []) or [])
        data_req_ids = list(dict.fromkeys(data_req_ids))
        covered_requirements.update(data_req_ids)
        acc_ids = []
        for req_id in data_req_ids:
            acc_ids.extend(_acc_for_req(req_id))
        wp = {
            "id": "wp-data",
            "title": "Implement data model",
            "node_type": "implementation",
            "agent": "backend-dev",
            "requirements": data_req_ids,
            "acceptance_criteria": list(dict.fromkeys(acc_ids)),
            "planning_basis": "design_spec",
        }
        _enrich_wp(wp, data_req_ids, "Data model implementation", requirements_data)
        work_packages.append(wp)

    # 4. Fallback: uncovered requirements
    all_req_ids = set(requirements_data.keys()) if requirements_data else {
        item.get("requirement_id")
        for item in plan.payload.get("requirement_coverage", []) or []
        if item.get("requirement_id")
    }
    uncovered = sorted(all_req_ids - covered_requirements)
    for req_id in uncovered:
        req_data = requirements_data.get(req_id, {})
        acc_ids = _acc_for_req(req_id)
        wp = {
            "id": f"wp-{slug(req_id)}",
            "title": f"Implement {req_id}",
            "node_type": "implementation",
            "agent": implementation_agent(template_id),
            "requirements": [req_id],
            "acceptance_criteria": acc_ids,
            "planning_basis": "requirement_ears",
        }
        _enrich_wp(wp, [req_id], str(req_data.get("system_response", "")), requirements_data)
        work_packages.append(wp)

    if not work_packages:
        return False, plan

    # 1.0 compatibility: assign unassigned acceptances (no requirement_id
    # mapping) to all WPs that have requirements, matching the 1.0 semantics
    # that any acceptance could apply to any requirement.  This ensures every
    # requirement_coverage entry has mapped acceptances in conformance.
    assigned_acc_ids: set[str] = set()
    for wp in work_packages:
        assigned_acc_ids.update(wp.get("acceptance_criteria", []))
    all_acc_ids: list[str] = []
    for idx, acc_item in enumerate(acceptance_items):
        acc_id = (
            str(acc_item.get("id", f"acc-{idx + 1}"))
            if isinstance(acc_item, dict)
            else f"acc-{idx + 1}"
        )
        all_acc_ids.append(acc_id)
    unassigned_acc_ids = [
        acc_id for acc_id in all_acc_ids if acc_id not in assigned_acc_ids
    ]
    if unassigned_acc_ids:
        for wp in work_packages:
            if wp.get("requirements"):
                wp["acceptance_criteria"].extend(unassigned_acc_ids)
                wp["acceptance_criteria"] = list(dict.fromkeys(wp["acceptance_criteria"]))

    # Build refined payload
    payload = {
        "schema_version": "1.0",
        "run_id": plan.payload.get("run_id"),
        "spec_id": plan.payload.get("spec_id"),
        "spec_version": plan.payload.get("spec_version"),
        "template_id": template_id,
        "work_packages": work_packages,
        "depends_on": [],
        "agent_hints": [
            {"work_package_id": wp["id"], "agent": wp["agent"]} for wp in work_packages
        ],
        "requirement_coverage": [
            {"requirement_id": req_id, "work_package_id": wp["id"]}
            for wp in work_packages
            for req_id in wp.get("requirements", [])
        ],
        "acceptance_coverage": [
            {"acceptance_id": acc_id, "work_package_id": wp["id"], "verification": "test"}
            for wp in work_packages
            for acc_id in wp.get("acceptance_criteria", [])
        ],
        "risk_tags": list(plan.payload.get("risk_tags", []) or []),
        "environment_requirements": list(plan.payload.get("environment_requirements", []) or []),
    }

    # Validate before writing — if invalid, keep original plan
    validation = validate_execution_plan_payload(payload, snapshot)
    if [i for i in validation.issues if i.severity in ("critical", "error")]:
        return False, plan

    write_json(plan_path, payload)
    EventLogger(root).append_run(
        run_id,
        "plan_refined_from_design_spec",
        {"run_id": run_id, "wp_count": len(work_packages), "planning_basis": "design_spec"},
    )
    return True, ExecutionPlan(path=plan_path, payload=payload, issues=[])
