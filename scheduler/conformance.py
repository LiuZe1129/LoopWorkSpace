from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .intent_audit import INTENT_INCOMPLETE, run_intent_audit
from .runs import GraphRunStore, read_json, relative_to_root, utc_now, write_json
from .specs import load_spec_snapshot
from .state import ValidationIssue, ValidationResult


CONFORMANCE_REQUIRED_FIELDS = [
    "schema_version",
    "run_id",
    "spec_id",
    "spec_version",
    "overall",
    "requirements",
    "acceptance_criteria",
    "unverified",
    "evidence_refs",
    "trace_matrix",
    "intent_audit",
    "generated_at",
]


@dataclass(frozen=True)
class ConformanceReport:
    path: Path | None
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)


def generate_conformance_report(root: Path, run_id: str, graph: dict[str, Any], completed_nodes: list[str]) -> ConformanceReport:
    store = GraphRunStore(root)
    completed = set(completed_nodes)
    requirement_items = []
    acceptance_items = []
    unverified = []
    evidence_refs = []

    # Load snapshot for requirement/acceptance metadata (priority, type, check/expect)
    state = store.read_state(run_id)
    snapshot = load_spec_snapshot(root / state.bound_spec.snapshot_uri)
    is_strict = str(snapshot.payload.get("schema_version", "1.0")) == "1.1"
    req_data_by_id: dict[str, dict[str, Any]] = {
        str(r.get("id", "")): r
        for r in (snapshot.payload.get("requirements", []) or [])
        if isinstance(r, dict)
    }
    acc_data_by_id: dict[str, dict[str, Any]] = {
        str(a.get("id", "")): a
        for a in (snapshot.payload.get("acceptance_criteria", []) or [])
        if isinstance(a, dict)
    }

    # Build wp → acceptance_ids mapping from acceptance_coverage
    wp_to_accs: dict[str, list[str]] = {}
    for coverage in graph.get("acceptance_coverage", []) or []:
        wp_id = str(coverage.get("work_package_id", ""))
        acc_id = str(coverage.get("acceptance_id", ""))
        wp_to_accs.setdefault(wp_id, []).append(acc_id)

    # --- Requirement flat list (enhanced: 禁止空验收自动 PASS) ---
    for coverage in graph.get("requirement_coverage", []) or []:
        requirement_id = str(coverage.get("requirement_id", ""))
        work_package_id = str(coverage.get("work_package_id", ""))
        node_id = f"{work_package_id}_implement"
        evidence_path = store.run_dir(run_id) / "artifacts" / f"{node_id}.json"
        mapped_accs = wp_to_accs.get(work_package_id, [])

        if not mapped_accs:
            status = "UNVERIFIED"
            reason = "no acceptance criteria mapped to this requirement"
        elif node_id in completed and evidence_path.is_file():
            status = "PASS"
            reason = ""
        else:
            status = "UNVERIFIED"
            reason = "missing implementation evidence"

        evidence = relative_to_root(root, evidence_path) if evidence_path.is_file() else None
        if evidence:
            evidence_refs.append(evidence)
        item = {
            "requirement_id": requirement_id,
            "status": status,
            "implementation_node": node_id,
            "evidence_refs": [evidence] if evidence else [],
        }
        requirement_items.append(item)
        if status != "PASS":
            unverified.append({"type": "requirement", "id": requirement_id, "reason": reason})

    # --- Acceptance flat list (enhanced: automated 空检查 → FAIL) ---
    for coverage in graph.get("acceptance_coverage", []) or []:
        acceptance_id = str(coverage.get("acceptance_id", ""))
        work_package_id = str(coverage.get("work_package_id", ""))
        node_id = f"{work_package_id}_test"
        evidence_path = store.run_dir(run_id) / "artifacts" / f"{node_id}.json"

        acc_data = acc_data_by_id.get(acceptance_id, {})
        acc_type = acc_data.get("type")
        is_empty_automated = (
            acc_type == "automated"
            and (not str(acc_data.get("check", "")).strip() or not str(acc_data.get("expect", "")).strip())
        )

        if is_empty_automated:
            status = "FAIL"
            reason = "automated acceptance has empty check or expect"
        elif node_id in completed and evidence_path.is_file():
            status = "PASS"
            reason = ""
        else:
            status = "UNVERIFIED"
            reason = "missing verification evidence"

        evidence = relative_to_root(root, evidence_path) if evidence_path.is_file() else None
        if evidence:
            evidence_refs.append(evidence)
        item = {
            "acceptance_id": acceptance_id,
            "status": status,
            "verification_node": node_id,
            "evidence_refs": [evidence] if evidence else [],
        }
        acceptance_items.append(item)
        if status != "PASS":
            unverified.append({"type": "acceptance", "id": acceptance_id, "reason": reason})

    # --- Trace matrix (new: structured Requirement → Acceptance → Node → Evidence) ---
    trace_matrix = []
    for coverage in graph.get("requirement_coverage", []) or []:
        req_id = str(coverage.get("requirement_id", ""))
        wp_id = str(coverage.get("work_package_id", ""))
        impl_node = f"{wp_id}_implement"
        test_node = f"{wp_id}_test"
        mapped_accs = wp_to_accs.get(wp_id, [])

        impl_evidence_path = store.run_dir(run_id) / "artifacts" / f"{impl_node}.json"
        test_evidence_path = store.run_dir(run_id) / "artifacts" / f"{test_node}.json"
        impl_ok = impl_node in completed and impl_evidence_path.is_file()
        test_ok = test_node in completed and test_evidence_path.is_file()

        req_data = req_data_by_id.get(req_id, {})
        priority = str(req_data.get("priority", "MUST")) if is_strict else "MUST"

        # FAIL: spec defect (orphan requirement or empty automated acceptance)
        if is_strict and not mapped_accs:
            tm_status = "FAIL"
            tm_reason = "requirement has no acceptance coverage (spec defect)"
        elif is_strict and any(
            (
                acc_data_by_id.get(aid, {}).get("type") == "automated"
                and (
                    not str(acc_data_by_id.get(aid, {}).get("check", "")).strip()
                    or not str(acc_data_by_id.get(aid, {}).get("expect", "")).strip()
                )
            )
            for aid in mapped_accs
        ):
            tm_status = "FAIL"
            tm_reason = "automated acceptance with empty check/expect (spec defect)"
        elif not mapped_accs:
            tm_status = "UNVERIFIED"
            tm_reason = "no acceptance criteria mapped (禁止空验收自动 PASS)"
        elif impl_ok and test_ok:
            tm_status = "PASS"
            tm_reason = ""
        else:
            tm_status = "UNVERIFIED"
            tm_reason = "missing implementation or test evidence"

        tm_evidence: list[str] = []
        if impl_evidence_path.is_file():
            tm_evidence.append(relative_to_root(root, impl_evidence_path))
        if test_evidence_path.is_file():
            tm_evidence.append(relative_to_root(root, test_evidence_path))

        trace_matrix.append({
            "requirement_id": req_id,
            "priority": priority,
            "acceptance_ids": mapped_accs,
            "implementation_nodes": [impl_node],
            "test_nodes": [test_node],
            "evidence_refs": tm_evidence,
            "status": tm_status,
            "reason": tm_reason,
        })

    intent_audit = run_intent_audit(root, run_id)

    # --- Overall computation (priority-aware for 1.1, fallback for 1.0) ---
    must_fail = any(t["status"] == "FAIL" and t["priority"] == "MUST" for t in trace_matrix)
    must_unverified = any(t["status"] == "UNVERIFIED" and t["priority"] == "MUST" for t in trace_matrix)

    if must_fail:
        overall = "FAIL"
    elif must_unverified:
        overall = "UNVERIFIED"
    elif intent_audit.status == INTENT_INCOMPLETE:
        overall = "GAP_FOUND"
    else:
        overall = "PASS"

    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "spec_id": graph.get("spec_id"),
        "spec_version": graph.get("spec_version"),
        "overall": overall,
        "requirements": requirement_items,
        "acceptance_criteria": acceptance_items,
        "unverified": unverified,
        "evidence_refs": sorted(set(evidence_refs)),
        "trace_matrix": trace_matrix,
        "intent_audit": intent_audit.to_report_section(),
        "generated_at": utc_now(),
    }
    result = validate_conformance_report_payload(payload)
    if not result.ok:
        return ConformanceReport(path=None, payload=payload, issues=result.issues)

    path = store.run_dir(run_id) / "conformance-report.json"
    write_json(path, payload)
    return ConformanceReport(path=path, payload=payload, issues=[])


def validate_conformance_report_payload(payload: dict[str, Any]) -> ValidationResult:
    result = ValidationResult(ok=True)
    for field_name in CONFORMANCE_REQUIRED_FIELDS:
        if payload.get(field_name) in (None, ""):
            result.add("conformance_report.missing_field", "error", f"missing conformance report field: {field_name}")

    if payload.get("overall") not in {"PASS", "FAIL", "UNVERIFIED", "GAP_FOUND"}:
        result.add("conformance_report.invalid_overall", "error", f"invalid overall: {payload.get('overall')}")

    if "intent_audit" in payload and not isinstance(payload["intent_audit"], dict):
        result.add("conformance_report.invalid_intent_audit", "error", "intent_audit must be an object")
    if isinstance(payload.get("intent_audit"), dict):
        audit_status = payload["intent_audit"].get("status")
        if audit_status not in {"NO_INTENT_DOCS", "INTENT_INCOMPLETE", "INTENT_PRESENT"}:
            result.add(
                "conformance_report.invalid_intent_audit_status",
                "error",
                f"invalid intent_audit.status: {audit_status}",
            )

    for field_name in ["requirements", "acceptance_criteria", "unverified", "evidence_refs", "trace_matrix"]:
        if field_name in payload and not isinstance(payload[field_name], list):
            result.add("conformance_report.invalid_list", "error", f"{field_name} must be a list")

    for item in payload.get("requirements", []) or []:
        if item.get("status") not in {"PASS", "FAIL", "UNVERIFIED"}:
            result.add("conformance_report.invalid_requirement_status", "error", f"invalid requirement status: {item.get('status')}")

    for item in payload.get("acceptance_criteria", []) or []:
        if item.get("status") not in {"PASS", "FAIL", "UNVERIFIED"}:
            result.add("conformance_report.invalid_acceptance_status", "error", f"invalid acceptance status: {item.get('status')}")

    for item in payload.get("trace_matrix", []) or []:
        if item.get("status") not in {"PASS", "FAIL", "UNVERIFIED"}:
            result.add("conformance_report.invalid_trace_status", "error", f"invalid trace_matrix status: {item.get('status')}")

    return result


def load_conformance_report(path: Path) -> ConformanceReport:
    payload = read_json(path)
    result = validate_conformance_report_payload(payload)
    return ConformanceReport(path=path, payload=payload, issues=result.issues)
