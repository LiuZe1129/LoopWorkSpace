from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import EventLogger
from .runs import GraphRunStore, RunState, relative_to_root, utc_now, write_json
from .state import ValidationIssue, ValidationResult


MANIFEST_REQUIRED_FIELDS = [
    "schema_version",
    "run_id",
    "spec_id",
    "spec_version",
    "generated_at",
    "artifacts",
    "run_files",
]


@dataclass(frozen=True)
class EvidenceManifest:
    path: Path | None
    payload: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)


def generate_evidence_manifest(root: Path, run_id: str, state: RunState | None = None) -> EvidenceManifest:
    store = GraphRunStore(root)
    selected_state = state or store.read_state(run_id)
    artifact_entries = []
    issues: list[ValidationIssue] = []
    seen_artifact_paths: set[str] = set()

    for ref in selected_state.artifact_refs:
        path = str(ref.get("path", ""))
        if not path or path in seen_artifact_paths:
            continue
        seen_artifact_paths.add(path)
        entry, entry_issues = manifest_entry(root, path, ref.get("type", "artifact"), ref.get("description", ""))
        artifact_entries.append(entry)
        issues.extend(entry_issues)

    run_file_entries = []
    for path in run_file_paths(store.run_dir(run_id)):
        if path.is_file():
            entry, entry_issues = manifest_entry(root, relative_to_root(root, path), "run-file", "GraphRun runtime file")
            run_file_entries.append(entry)
            issues.extend(entry_issues)

    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "spec_id": selected_state.bound_spec.spec_id,
        "spec_version": selected_state.bound_spec.version,
        "generated_at": utc_now(),
        "artifacts": artifact_entries,
        "run_files": run_file_entries,
    }
    result = validate_evidence_manifest_payload(payload)
    issues.extend(result.issues)
    if issues:
        return EvidenceManifest(path=None, payload=payload, issues=issues)

    path = store.run_dir(run_id) / "evidence-manifest.json"
    write_json(path, payload)
    EventLogger(root).append_run(
        run_id,
        "evidence_manifest_written",
        {
            "run_id": run_id,
            "path": relative_to_root(root, path),
            "artifact_count": len(artifact_entries),
            "run_file_count": len(run_file_entries),
        },
    )
    return EvidenceManifest(path=path, payload=payload, issues=[])


def validate_evidence_manifest_payload(payload: dict[str, Any]) -> ValidationResult:
    result = ValidationResult(ok=True)
    for field_name in MANIFEST_REQUIRED_FIELDS:
        if payload.get(field_name) in (None, ""):
            result.add("evidence_manifest.missing_field", "error", f"missing evidence manifest field: {field_name}")
    for field_name in ["artifacts", "run_files"]:
        if field_name in payload and not isinstance(payload[field_name], list):
            result.add("evidence_manifest.invalid_list", "error", f"{field_name} must be a list")
    for section in ["artifacts", "run_files"]:
        for item in payload.get(section, []) or []:
            for field_name in ["path", "type", "sha256", "size_bytes"]:
                if item.get(field_name) in (None, ""):
                    result.add("evidence_manifest.invalid_entry", "error", f"{section} entry missing field: {field_name}")
            if item.get("sha256") and not str(item["sha256"]).startswith("sha256:"):
                result.add("evidence_manifest.invalid_hash", "error", f"{section} entry has invalid hash: {item.get('path')}")
    return result


def manifest_entry(root: Path, relative_path: str, artifact_type: str, description: str) -> tuple[dict[str, Any], list[ValidationIssue]]:
    candidate = Path(relative_path)
    path = candidate if candidate.is_absolute() else root / candidate
    if not path.is_file():
        return (
            {
                "path": relative_path,
                "type": artifact_type,
                "description": description,
                "sha256": "",
                "size_bytes": 0,
            },
            [
                ValidationIssue(
                    code="evidence_manifest.missing_file",
                    severity="critical",
                    message=f"manifest file does not exist: {relative_path}",
                    path=relative_path,
                )
            ],
        )
    data = path.read_bytes()
    return (
        {
            "path": relative_to_root(root, path),
            "type": artifact_type,
            "description": description,
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        },
        [],
    )


def run_file_paths(run_dir: Path) -> list[Path]:
    paths = [
        run_dir / "spec-binding.json",
        run_dir / "run-state.json",
        run_dir / "execution-plan.json",
        run_dir / "execution-graph.json",
        run_dir / "tool-contract-report.json",
        run_dir / "conformance-report.json",
    ]
    design_spec = run_dir / "design-spec.json"
    if design_spec.is_file():
        paths.append(design_spec)
    return paths
