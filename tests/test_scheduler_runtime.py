from __future__ import annotations

import tempfile
import unittest
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scheduler.artifacts import ArtifactManager
from scheduler.audit import audit_repository
from scheduler.conformance import generate_conformance_report, validate_conformance_report_payload
from scheduler.design_spec import (
    generate_minimal_design_spec,
    load_design_spec,
    run_design_gate_checks,
    validate_design_spec_payload,
    write_design_spec,
)
from scheduler.drg import compile_execution_graph, validate_execution_graph_payload
from scheduler.evidence import generate_evidence_manifest, validate_evidence_manifest_payload
from scheduler.executor import DRGExecutor, NodeExecutionResult
from scheduler.graph import GRAPHFLOW_EDGES
from scheduler.intent_audit import INTENT_INCOMPLETE, INTENT_PRESENT, NO_INTENT_DOCS, run_intent_audit
from scheduler.legacy import convert_task_card_to_change_spec
from scheduler.main import main as scheduler_main
from scheduler.plans import generate_execution_plan, is_rich_design_spec, refine_plan_from_design_spec, validate_execution_plan_payload
from scheduler.retry import RetryManager, downstream_nodes
from scheduler.retry_policy import RetryPolicy, load_retry_policy
from scheduler.runs import GraphRunStore
from scheduler.schema import validate_ui_design_contract
from scheduler.spec_flow import (
    approve_revision_draft,
    create_revision_draft_from_scr,
    load_change_spec,
    publish_change_spec,
    validate_change_spec_payload,
    validate_product_spec_payload,
)
from scheduler.spec_change import validate_spec_change_request_payload
from scheduler.specs import calculate_content_hash, load_spec_snapshot
from scheduler.supersede import SupersedeManager
from scheduler.task_card import load_task_card
from scheduler.tool_contracts import generate_tool_contract_report, validate_tool_contracts
from scheduler.transition import TransitionValidator
from scheduler.ui_design import validate_product_shape


TASK_CARD = """---
id: task-001
project: web-app
status: DONE
assigned_to: null
executed_agents: []
executed_agents_arch: []
executed_agents_subtask: []
current_subtask: 0
arch_round: 1
phase: development
env_check_done: true
pre_awaiting_human_status: null
project_type: web
round: 1
retry_count: 0
dependencies: []
artifacts: []
created_at: 2026-08-19T00:00:00Z
updated_at: 2026-08-19T00:00:00Z
---

# Task: Demo

## Goal
Demo goal.

## Done（验收标准）
- id: d1
  check: "echo ok"
  expect: "ok"
  status: pending
  gate_stage: final
"""

FRONTEND_TASK = """---
id: task-001
project: web-app
status: DISPATCHED
assigned_to: frontend-dev
executed_agents: []
executed_agents_arch: []
executed_agents_subtask: []
current_subtask: 0
arch_round: 1
phase: development
env_check_done: true
pre_awaiting_human_status: null
pre_awaiting_human_node: null
graph_node: implementation_drg_node
project_type: web
round: 1
retry_count: 0
dependencies: []
artifacts: []
created_at: 2026-08-19T00:00:00Z
updated_at: 2026-08-19T00:00:00Z
---

# Task: Frontend

## Goal
Demo goal.

## Done（验收标准）
- id: d1
  check: "echo ok"
  expect: "ok"
  status: passed
  gate_stage: final
"""

ENV_CHECK_TASK = """---
id: task-001
project: web-app
status: AWAITING_REVIEW
assigned_to: null
executed_agents: []
executed_agents_arch: []
executed_agents_subtask: []
current_subtask: 0
arch_round: 1
phase: architecture
env_check_done: true
pre_awaiting_human_status: null
pre_awaiting_human_node: null
graph_node: architecture_gate_node
project_type: web
round: 1
retry_count: 0
dependencies: []
artifacts: []
created_at: 2026-08-19T00:00:00Z
updated_at: 2026-08-19T00:00:00Z
---

# Task: Env

## Goal
Demo goal.

## Done（验收标准）
- id: d1
  check: "echo ok"
  expect: "ok"
  status: passed
  gate_stage: final
"""


def spec_snapshot_payload(status: str = "PUBLISHED", acceptance_check: dict | None = None) -> dict:
    acceptance = {"id": "a1", "summary": "Demo is visible"}
    if acceptance_check:
        acceptance.update(acceptance_check)
    payload = {
        "schema_version": "1.0",
        "spec_id": "task-001",
        "version": 1,
        "status": status,
        "published_at": "2026-08-24T10:00:00+08:00",
        "content_hash": "",
        "project": "web-app",
        "intent": {"summary": "Demo"},
        "scope": {"in": ["demo"], "out": []},
        "requirements": [{"id": "r1", "summary": "Render demo"}],
        "acceptance_criteria": [acceptance],
        "constraints": [],
        "dependencies": [],
        "product_shape": {"product_type": "web"},
        "source_refs": [],
    }
    payload["content_hash"] = calculate_content_hash(payload)
    return payload


def write_snapshot(root: Path, payload: dict | None = None) -> Path:
    selected_payload = payload or spec_snapshot_payload()
    snapshot_path = (
        root
        / "specs"
        / "web-app"
        / "published"
        / "task-001"
        / f"v{selected_payload['version']}"
        / "spec-snapshot.json"
    )
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(json.dumps(selected_payload, ensure_ascii=False), encoding="utf-8")
    return snapshot_path


def change_spec_payload(open_questions: list[str] | None = None) -> dict:
    return {
        "schema_version": "1.0",
        "spec_id": "task-001",
        "project": "web-app",
        "intent": {"summary": "Demo"},
        "scope": {"in": ["demo"], "out": []},
        "requirements": [{"id": "r1", "summary": "Render demo"}],
        "acceptance_criteria": [{"id": "a1", "summary": "Demo is visible", "check": "printf ok", "expect": "ok"}],
        "constraints": ["change constraint"],
        "dependencies": [],
        "assumptions": [],
        "open_questions": open_questions or [],
        "product_shape": {"product_type": "web"},
        "source_refs": [],
    }


def product_spec_payload() -> dict:
    return {
        "schema_version": "1.0",
        "project": "web-app",
        "product_type": "web-app",
        "target_users": ["admin"],
        "core_workflows": ["demo"],
        "constraints": ["product constraint"],
        "glossary": [],
    }


def write_change_spec(root: Path, payload: dict | None = None) -> Path:
    path = root / "specs" / "web-app" / "drafts" / "task-001-change-spec.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload or change_spec_payload(), ensure_ascii=False), encoding="utf-8")
    return path


def write_product_spec(root: Path, payload: dict | None = None) -> Path:
    path = root / "specs" / "web-app" / "product-spec.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload or product_spec_payload(), ensure_ascii=False), encoding="utf-8")
    return path


class SchedulerRuntimeTests(unittest.TestCase):
    def test_empty_repository_audit_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".loop" / "active").mkdir(parents=True)
            (root / ".loop" / "done").mkdir(parents=True)
            self.assertEqual(audit_repository(root), [])

    def test_done_task_with_pending_item_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = root / ".loop" / "active" / "web-app"
            task_dir.mkdir(parents=True)
            (root / ".loop" / "done").mkdir(parents=True)
            task_path = task_dir / "task-001-demo.md"
            task_path.write_text(TASK_CARD, encoding="utf-8")

            issues = audit_repository(root)
            codes = {issue.code for issue in issues}
            self.assertIn("done.unpassed_items", codes)
            self.assertIn("done.missing_artifact", codes)

    def test_task_card_loader_parses_done_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task-001-demo.md"
            path.write_text(TASK_CARD, encoding="utf-8")
            task = load_task_card(path)
            self.assertEqual(task.id, "task-001")
            self.assertEqual(task.done_items[0].gate_stage, "final")

    def test_graph_has_retry_and_spec_change_edges(self) -> None:
        edge_pairs = {(edge.source, edge.target) for edge in GRAPHFLOW_EDGES}
        self.assertIn(("final_gate_node", "retry_node"), edge_pairs)
        self.assertIn(("design_gate_node", "drg_compile_node"), edge_pairs)
        self.assertIn(("spec_binding_node", "planning_node"), edge_pairs)
        self.assertIn(("conformance_node", "awaiting_spec_change_node"), edge_pairs)

    def test_product_shape_requires_resolved_questions(self) -> None:
        missing = validate_product_shape(
            {
                "product_type": "web-app",
                "target_users": ["admin"],
                "core_workflows": ["login"],
                "information_architecture": ["dashboard"],
                "interaction_model": ["form submit"],
                "visual_direction": "quiet operational dashboard",
                "open_questions": ["Which roles are required?"],
            }
        )
        self.assertIn("open_questions must be resolved before UI design approval", missing)

    def test_env_check_no_longer_transitions_directly_to_dispatched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_path = root / "task-001-env.md"
            task_path.write_text(ENV_CHECK_TASK, encoding="utf-8")
            task = load_task_card(task_path)

            check = TransitionValidator(ArtifactManager(root)).validate(task, "DISPATCHED")
            self.assertFalse(check.allowed)
            self.assertEqual(check.issues[0].code, "transition.illegal")

    def test_frontend_requires_approved_ui_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_path = root / "task-001-frontend.md"
            task_path.write_text(FRONTEND_TASK, encoding="utf-8")
            task = load_task_card(task_path)
            artifact_dir = root / ".loop" / "artifacts" / "web-app" / "task-001" / "round-1"
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "product-shape.json").write_text("{}", encoding="utf-8")
            (artifact_dir / "ui-design.json").write_text("{}", encoding="utf-8")
            (artifact_dir / "ui-review.json").write_text(json.dumps({"verdict": "REVISE"}), encoding="utf-8")

            check = TransitionValidator(ArtifactManager(root)).validate(task, "RUNNING")
            codes = {issue.code for issue in check.issues}
            self.assertFalse(check.allowed)
            self.assertIn("transition.ui_review_not_approved", codes)

    def test_done_requires_product_shape_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = root / ".loop" / "active" / "web-app"
            task_dir.mkdir(parents=True)
            (root / ".loop" / "done").mkdir(parents=True)
            task_path = task_dir / "task-001-demo.md"
            task_path.write_text(TASK_CARD, encoding="utf-8")

            issues = audit_repository(root)
            missing_messages = [issue.message for issue in issues if issue.code == "done.missing_artifact"]
            self.assertTrue(any("product-shape.json" in message for message in missing_messages))

    def test_spec_snapshot_validates_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = load_spec_snapshot(write_snapshot(Path(tmp)))
            self.assertEqual(snapshot.issues, [])
            self.assertEqual(snapshot.spec_id, "task-001")

    def test_change_spec_publish_creates_immutable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            change_path = write_change_spec(root)

            result = publish_change_spec(root, change_path, version=1)

            self.assertEqual(result.issues, [])
            self.assertIsNotNone(result.path)
            assert result.path is not None
            self.assertTrue(result.path.is_file())
            snapshot = load_spec_snapshot(result.path)
            self.assertEqual(snapshot.issues, [])
            self.assertEqual(snapshot.status, "PUBLISHED")
            self.assertEqual(snapshot.version, 1)

    def test_change_spec_open_questions_block_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            change_path = write_change_spec(root, change_spec_payload(open_questions=["Which role?"]))

            draft = load_change_spec(change_path)
            result = publish_change_spec(root, change_path, version=1)

            self.assertTrue(draft.issues)
            self.assertTrue(any(issue.code == "change_spec.open_questions_unresolved" for issue in result.issues))

    def test_publish_refuses_to_overwrite_existing_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            change_path = write_change_spec(root)
            first = publish_change_spec(root, change_path, version=1)
            second = publish_change_spec(root, change_path, version=1)

            self.assertEqual(first.issues, [])
            self.assertTrue(any(issue.code == "publish_gate.snapshot_exists" for issue in second.issues))

    def test_publish_merges_product_spec_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            change_path = write_change_spec(root)
            product_path = write_product_spec(root)

            self.assertTrue(validate_product_spec_payload(product_spec_payload()).ok)
            result = publish_change_spec(root, change_path, version=1, product_spec_path=product_path)

            self.assertEqual(result.issues, [])
            self.assertIn("change constraint", result.payload["constraints"])
            self.assertIn("product constraint", result.payload["constraints"])

    def test_legacy_task_card_converts_to_publishable_change_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = root / ".loop" / "active" / "web-app"
            task_dir.mkdir(parents=True)
            task_path = task_dir / "task-001-demo.md"
            task_path.write_text(TASK_CARD, encoding="utf-8")

            result = convert_task_card_to_change_spec(root, task_path)

            self.assertEqual(result.issues, [])
            self.assertIsNotNone(result.path)
            assert result.path is not None
            self.assertTrue(result.path.is_file())
            self.assertEqual(result.payload["spec_id"], "task-001")
            self.assertEqual(result.payload["project"], "web-app")
            self.assertEqual(result.payload["acceptance_criteria"][0]["check"], "echo ok")
            self.assertEqual(result.payload["acceptance_criteria"][0]["expect"], "ok")
            self.assertEqual(result.payload["source_refs"], [".loop/active/web-app/task-001-demo.md"])

            publish = publish_change_spec(root, result.path, version=1)
            self.assertEqual(publish.issues, [])
            self.assertIsNotNone(publish.path)

    def test_legacy_task_card_without_done_items_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_path = root / "task-001-demo.md"
            task_path.write_text(TASK_CARD.split("## Done")[0], encoding="utf-8")

            result = convert_task_card_to_change_spec(root, task_path)

            self.assertIsNone(result.path)
            self.assertTrue(any(issue.code == "legacy_task.no_done_items" for issue in result.issues))

    def test_smoke_flow_command_runs_change_spec_to_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            change_path = write_change_spec(root)
            argv = [
                "scheduler.main",
                "--root",
                str(root),
                "smoke-flow",
                str(change_path.relative_to(root)),
                "--run-id",
                "run-smoke",
            ]

            with patch("sys.argv", argv), redirect_stdout(StringIO()):
                exit_code = scheduler_main()

            run_dir = root / ".loop" / "runs" / "run-smoke"
            self.assertEqual(exit_code, 0)
            self.assertTrue((run_dir / "run-state.json").is_file())
            self.assertTrue((run_dir / "execution-plan.json").is_file())
            self.assertTrue((run_dir / "execution-graph.json").is_file())
            self.assertTrue((run_dir / "tool-contract-report.json").is_file())
            self.assertTrue((run_dir / "evidence-manifest.json").is_file())
            state = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "DONE")

    def test_graph_run_rejects_unpublished_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = spec_snapshot_payload(status="DRAFT")
            payload["content_hash"] = calculate_content_hash(payload)
            snapshot_path = write_snapshot(root, payload)

            with self.assertRaises(ValueError):
                GraphRunStore(root).create(snapshot_path, run_id="run-001")

    def test_graph_run_creation_writes_binding_state_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)

            state = store.create(snapshot_path, run_id="run-001")
            run_dir = root / ".loop" / "runs" / "run-001"

            self.assertEqual(state.status, "BOUND")
            self.assertTrue((run_dir / "spec-binding.json").is_file())
            self.assertTrue((run_dir / "run-state.json").is_file())
            self.assertTrue((run_dir / "events.jsonl").is_file())
            self.assertTrue((run_dir / "state-snapshots" / "000-created.json").is_file())
            self.assertTrue(store.validate("run-001").ok)

            events = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(events), 2)
            self.assertEqual(json.loads(events[0])["type"], "run_created")
            self.assertEqual(json.loads(events[1])["type"], "spec_bound")

    def test_execution_plan_covers_snapshot_requirements_and_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            GraphRunStore(root).create(snapshot_path, run_id="run-001")

            plan = generate_execution_plan(root, "run-001")

            self.assertEqual(plan.issues, [])
            self.assertEqual(plan.payload["template_id"], "web-feature-v1")
            self.assertTrue((root / ".loop" / "runs" / "run-001" / "execution-plan.json").is_file())
            snapshot = load_spec_snapshot(snapshot_path)
            self.assertTrue(validate_execution_plan_payload(plan.payload, snapshot).ok)

    def test_execution_plan_detects_missing_acceptance_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = load_spec_snapshot(write_snapshot(root))
            payload = {
                "schema_version": "1.0",
                "run_id": "run-001",
                "spec_id": "task-001",
                "spec_version": 1,
                "template_id": "web-feature-v1",
                "work_packages": [
                    {
                        "id": "wp-r1",
                        "title": "Implement r1",
                        "node_type": "implementation",
                        "agent": "frontend-dev",
                        "requirements": ["r1"],
                        "acceptance_criteria": [],
                    }
                ],
                "depends_on": [],
                "agent_hints": [],
                "requirement_coverage": [{"requirement_id": "r1", "work_package_id": "wp-r1"}],
                "acceptance_coverage": [],
                "risk_tags": [],
                "environment_requirements": [],
            }

            result = validate_execution_plan_payload(payload, snapshot)
            codes = {issue.code for issue in result.issues}
            self.assertFalse(result.ok)
            self.assertIn("execution_plan.missing_acceptance_coverage", codes)

    def test_drg_compiler_writes_valid_execution_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            GraphRunStore(root).create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")

            graph = compile_execution_graph(root, "run-001")

            self.assertEqual(graph.issues, [])
            self.assertTrue((root / ".loop" / "runs" / "run-001" / "execution-graph.json").is_file())
            self.assertTrue(validate_execution_graph_payload(graph.payload).ok)
            node_ids = {node["id"] for node in graph.payload["nodes"]}
            self.assertIn("spec_binding_node", node_ids)
            self.assertIn("conformance_node", node_ids)
            self.assertIn("wp-r1_implement", node_ids)
            self.assertIn("wp-r1_test", node_ids)

    def test_drg_validator_rejects_cycles(self) -> None:
        payload = {
            "schema_version": "1.0",
            "run_id": "run-001",
            "spec_id": "task-001",
            "spec_version": 1,
            "template_id": "web-feature-v1",
            "content_hash": "",
            "nodes": [
                {"id": node_id, "type": "governance", "requirements": [], "acceptance_criteria": []}
                for node_id in [
                    "spec_binding_node",
                    "planning_node",
                    "plan_gate_node",
                    "architecture_impact_node",
                    "architecture_gate_node",
                    "design_spec_node",
                    "design_gate_node",
                    "drg_compile_node",
                    "implementation_drg_node",
                    "final_gate_node",
                    "conformance_node",
                    "delivery_review_node",
                    "deploy_node",
                    "done_node",
                ]
            ],
            "edges": [
                {"source": "spec_binding_node", "target": "planning_node"},
                {"source": "planning_node", "target": "spec_binding_node"},
            ],
            "requirement_coverage": [],
            "acceptance_coverage": [],
        }

        result = validate_execution_graph_payload(payload)
        codes = {issue.code for issue in result.issues}
        self.assertFalse(result.ok)
        self.assertIn("execution_graph.cycle", codes)

    def test_tool_contract_report_covers_compiled_graph_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            GraphRunStore(root).create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            graph = compile_execution_graph(root, "run-001")

            report = generate_tool_contract_report(root, "run-001", graph.payload)

            self.assertEqual(report.issues, [])
            self.assertEqual(report.payload["overall"], "PASS")
            self.assertTrue((root / ".loop" / "runs" / "run-001" / "tool-contract-report.json").is_file())
            entry = next(item for item in report.payload["nodes"] if item["node_id"] == "wp-r1_test")
            self.assertEqual(entry["agent"], "test-agent")
            self.assertIn("shell_test", entry["contract"]["allowed_tools"])
            self.assertEqual(entry["required_artifact_types"], ["test-result"])

    def test_tool_contract_rejects_unknown_agent(self) -> None:
        payload = {
            "nodes": [{"id": "custom_node", "type": "implementation", "agent": "unknown-agent"}],
            "edges": [],
        }

        result = validate_tool_contracts(payload, "run-001")

        self.assertFalse(result.ok)
        self.assertTrue(any(issue.code == "tool_contract.unknown_agent" for issue in result.issues))

    def test_tool_contract_rejects_node_type_outside_agent_contract(self) -> None:
        payload = {
            "nodes": [{"id": "bad_review", "type": "implementation", "agent": "review-agent"}],
            "edges": [],
        }

        result = validate_tool_contracts(payload, "run-001")

        self.assertFalse(result.ok)
        self.assertTrue(any(issue.code == "tool_contract.node_type_not_allowed" for issue in result.issues))

    def test_drg_executor_runs_graph_to_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            graph = compile_execution_graph(root, "run-001")

            result = DRGExecutor(root).run("run-001")
            state = store.read_state("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"

            self.assertEqual(result.status, "DONE")
            self.assertEqual(state.status, "DONE")
            self.assertEqual(len(state.completed_nodes), len(graph.payload["nodes"]))
            self.assertTrue((run_dir / "artifacts" / "done_node.json").is_file())
            self.assertTrue((run_dir / "state-snapshots" / "999-done.json").is_file())
            events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            event_types = [event["type"] for event in events]
            self.assertIn("node_started", event_types)
            self.assertIn("node_completed", event_types)
            self.assertIn("artifact_written", event_types)
            self.assertEqual(event_types[-1], "run_done")
            report_path = run_dir / "conformance-report.json"
            self.assertTrue(report_path.is_file())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["overall"], "PASS")
            self.assertEqual(report["requirements"][0]["status"], "PASS")
            self.assertEqual(report["acceptance_criteria"][0]["status"], "PASS")
            self.assertTrue(validate_conformance_report_payload(report).ok)
            tool_contract_path = run_dir / "tool-contract-report.json"
            self.assertTrue(tool_contract_path.is_file())
            tool_contract = json.loads(tool_contract_path.read_text(encoding="utf-8"))
            self.assertEqual(tool_contract["overall"], "PASS")
            manifest_path = run_dir / "evidence-manifest.json"
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(validate_evidence_manifest_payload(manifest).ok)
            self.assertTrue(any(item["path"].endswith("done_node.json") for item in manifest["artifacts"]))
            self.assertTrue(any(item["path"].endswith("conformance-report.json") for item in manifest["run_files"]))
            self.assertTrue(all(item["sha256"].startswith("sha256:") for item in manifest["artifacts"]))
            self.assertIn("evidence_manifest_written", event_types)

    def test_drg_executor_can_pause_after_max_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            result = DRGExecutor(root).run("run-001", max_steps=1)
            state = store.read_state("run-001")

            self.assertEqual(result.status, "RUNNING")
            self.assertEqual(state.completed_nodes, ["spec_binding_node"])
            self.assertEqual(state.current_nodes, ["planning_node"])
            self.assertIn("plan_gate_node", state.blocked_nodes)
            context_path = root / ".loop" / "runs" / "run-001" / "context-packs" / "spec_binding_node.json"
            self.assertTrue(context_path.is_file())
            context = json.loads(context_path.read_text(encoding="utf-8"))
            self.assertEqual(context["node"]["agent"], "gate-auto")
            self.assertEqual(context["bound_spec"]["version"], 1)
            self.assertIn("tool_contract_report", context["input_refs"])

    def test_evidence_manifest_rejects_missing_artifact_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            state = store.create(snapshot_path, run_id="run-001")
            state.artifact_refs = [
                {
                    "type": "node-result",
                    "path": ".loop/runs/run-001/artifacts/missing.json",
                    "description": "missing artifact",
                }
            ]
            store.write_state(state)

            manifest = generate_evidence_manifest(root, "run-001", state)

            self.assertTrue(any(issue.code == "evidence_manifest.missing_file" for issue in manifest.issues))
            self.assertIsNone(manifest.path)

    def test_conformance_report_marks_missing_evidence_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            GraphRunStore(root).create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            graph = compile_execution_graph(root, "run-001")

            report = generate_conformance_report(root, "run-001", graph.payload, completed_nodes=[])

            self.assertEqual(report.issues, [])
            self.assertEqual(report.payload["overall"], "UNVERIFIED")
            self.assertEqual(report.payload["requirements"][0]["status"], "UNVERIFIED")
            self.assertEqual(report.payload["acceptance_criteria"][0]["status"], "UNVERIFIED")
            self.assertEqual(len(report.payload["unverified"]), 2)

    def test_conformance_gap_raises_spec_change_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            graph = compile_execution_graph(root, "run-001")
            state = store.read_state("run-001")
            state.status = "RUNNING"
            state.completed_nodes = [
                node["id"]
                for node in graph.payload["nodes"]
                if node["id"] not in {"conformance_node", "delivery_review_node", "deploy_node", "done_node"}
            ]
            store.write_state(state)

            result = DRGExecutor(root).run("run-001", max_steps=1)
            state = store.read_state("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"
            scr_path = run_dir / "spec-change-request.json"

            self.assertEqual(result.status, "AWAITING_SPEC_CHANGE")
            self.assertEqual(state.status, "AWAITING_SPEC_CHANGE")
            self.assertTrue(scr_path.is_file())
            scr = json.loads(scr_path.read_text(encoding="utf-8"))
            self.assertTrue(validate_spec_change_request_payload(scr).ok)
            self.assertEqual(scr["type"], "spec_gap")
            self.assertIn("r1", scr["affected_requirements"])
            self.assertIn("a1", scr["affected_acceptance_criteria"])
            events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["type"], "spec_change_requested")

    def test_spec_change_request_creates_revision_draft_and_requires_approval_before_v2_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            change_path = write_change_spec(root)
            published = publish_change_spec(root, change_path, version=1)
            assert published.path is not None
            store = GraphRunStore(root)
            store.create(published.path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            graph = compile_execution_graph(root, "run-001")
            state = store.read_state("run-001")
            state.status = "RUNNING"
            state.completed_nodes = [
                node["id"]
                for node in graph.payload["nodes"]
                if node["id"] not in {"conformance_node", "delivery_review_node", "deploy_node", "done_node"}
            ]
            store.write_state(state)
            DRGExecutor(root).run("run-001", max_steps=1)
            scr_path = root / ".loop" / "runs" / "run-001" / "spec-change-request.json"

            draft = create_revision_draft_from_scr(root, scr_path, published.path)
            self.assertEqual(draft.issues, [])
            assert draft.path is not None
            self.assertTrue(draft.path.is_file())
            self.assertEqual(draft.payload["intent"]["base_version"], 1)
            self.assertTrue(draft.payload["requirements"][0]["revision_requested"])
            self.assertTrue(draft.payload["acceptance_criteria"][0]["revision_requested"])

            blocked = publish_change_spec(root, draft.path, version=2)
            self.assertTrue(any(issue.code == "publish_gate.revision_not_approved" for issue in blocked.issues))

            approval = approve_revision_draft(root, draft.path, approved_by="spec-owner", reason="SCR reviewed")
            self.assertEqual(approval.issues, [])
            self.assertEqual(approval.payload["approval"]["status"], "APPROVED")
            self.assertEqual(approval.payload["approval"]["approved_by"], "spec-owner")

            v2 = publish_change_spec(root, draft.path, version=2)
            self.assertEqual(v2.issues, [])
            assert v2.path is not None
            snapshot = load_spec_snapshot(v2.path)
            self.assertEqual(snapshot.version, 2)
            self.assertEqual(snapshot.status, "PUBLISHED")
            self.assertEqual(snapshot.payload["approval"]["approved_by"], "spec-owner")

    def test_revision_approval_rejects_non_revision_change_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            change_path = write_change_spec(root)

            approval = approve_revision_draft(root, change_path, approved_by="spec-owner")

            self.assertTrue(any(issue.code == "revision_approval.not_revision" for issue in approval.issues))

    def test_revision_draft_rejects_scr_base_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            change_path = write_change_spec(root)
            published = publish_change_spec(root, change_path, version=1)
            assert published.path is not None
            scr_path = root / ".loop" / "runs" / "run-001" / "spec-change-request.json"
            scr_path.parent.mkdir(parents=True)
            scr_path.write_text(json.dumps({
                "schema_version": "1.0",
                "run_id": "run-001",
                "spec_id": "task-001",
                "base_version": 2,
                "raised_by": "gate-auto",
                "type": "spec_gap",
                "reason": "mismatch",
                "affected_requirements": ["r1"],
                "affected_acceptance_criteria": ["a1"],
                "affected_nodes": ["conformance_node"],
                "evidence_refs": [],
                "created_at": "2026-08-24T00:00:00Z",
            }), encoding="utf-8")

            draft = create_revision_draft_from_scr(root, scr_path, published.path)

            self.assertTrue(any(issue.code == "revision_draft.base_mismatch" for issue in draft.issues))

    def test_supersede_run_creates_new_run_without_rebinding_old_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v1_path = write_snapshot(root)
            v2_payload = spec_snapshot_payload()
            v2_payload["version"] = 2
            v2_payload["content_hash"] = calculate_content_hash(v2_payload)
            v2_path = write_snapshot(root, v2_payload)
            store = GraphRunStore(root)
            store.create(v1_path, run_id="run-001")

            result = SupersedeManager(root).supersede("run-001", v2_path, new_run_id="run-002")

            self.assertEqual(result.issues, [])
            self.assertEqual(result.old_status, "SUPERSEDED")
            self.assertEqual(result.new_run_id, "run-002")
            old_state = store.read_state("run-001")
            new_state = store.read_state("run-002")
            self.assertEqual(old_state.status, "SUPERSEDED")
            self.assertEqual(old_state.bound_spec.version, 1)
            self.assertEqual(new_state.status, "BOUND")
            self.assertEqual(new_state.bound_spec.version, 2)
            old_binding = json.loads((root / ".loop" / "runs" / "run-001" / "spec-binding.json").read_text(encoding="utf-8"))
            self.assertEqual(old_binding["bound_spec"]["version"], 1)
            self.assertTrue((root / ".loop" / "runs" / "run-001" / "state-snapshots" / "999-superseded.json").is_file())
            old_events = [
                json.loads(line)
                for line in (root / ".loop" / "runs" / "run-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            new_events = [
                json.loads(line)
                for line in (root / ".loop" / "runs" / "run-002" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(old_events[-1]["type"], "run_superseded")
            self.assertEqual(old_events[-1]["new_run_id"], "run-002")
            self.assertEqual(new_events[-1]["type"], "run_supersedes")
            self.assertEqual(new_events[-1]["superseded_run_id"], "run-001")

    def test_supersede_run_rejects_same_or_older_snapshot_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v1_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(v1_path, run_id="run-001")

            result = SupersedeManager(root).supersede("run-001", v1_path, new_run_id="run-002")

            self.assertTrue(any(issue.code == "supersede.version_not_newer" for issue in result.issues))
            self.assertEqual(store.read_state("run-001").status, "BOUND")
            self.assertFalse((root / ".loop" / "runs" / "run-002").exists())

    def test_supersede_run_rejects_existing_new_run_id_without_changing_old_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v1_path = write_snapshot(root)
            v2_payload = spec_snapshot_payload()
            v2_payload["version"] = 2
            v2_payload["content_hash"] = calculate_content_hash(v2_payload)
            v2_path = write_snapshot(root, v2_payload)
            store = GraphRunStore(root)
            store.create(v1_path, run_id="run-001")
            store.create(v2_path, run_id="run-002")

            result = SupersedeManager(root).supersede("run-001", v2_path, new_run_id="run-002")

            self.assertTrue(any(issue.code == "supersede.new_run_exists" for issue in result.issues))
            self.assertEqual(store.read_state("run-001").status, "BOUND")

    def test_retry_reset_invalidates_node_and_downstream_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            graph = compile_execution_graph(root, "run-001")
            DRGExecutor(root).run("run-001")

            result = RetryManager(root).reset_node("run-001", "wp-r1_implement")
            state = store.read_state("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"

            expected = downstream_nodes(graph.payload, "wp-r1_implement") | {"wp-r1_implement"}
            self.assertEqual(set(result.invalidated_nodes), expected)
            self.assertEqual(state.status, "RUNNING")
            self.assertIn("wp-r1_implement", state.current_nodes)
            self.assertNotIn("wp-r1_implement", state.completed_nodes)
            self.assertNotIn("spec_binding_node", result.invalidated_nodes)
            self.assertEqual(state.retry_count["wp-r1_implement"], 1)
            self.assertFalse((run_dir / "artifacts" / "wp-r1_implement.json").exists())
            self.assertFalse((run_dir / "artifacts" / "done_node.json").exists())
            self.assertFalse((run_dir / "conformance-report.json").exists())
            events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["type"], "node_invalidated")

    def test_retry_reset_rejects_unknown_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            GraphRunStore(root).create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            result = RetryManager(root).reset_node("run-001", "missing_node")

            self.assertEqual(result.issues[0].code, "retry.unknown_node")

    def test_executor_schedules_retry_on_retryable_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            def fail_node(run_id, graph, state, node_payload):
                return NodeExecutionResult(status="failed", summary="temporary failure", error="temporary failure")

            result = DRGExecutor(root, node_handlers={"wp-r1_implement": fail_node}).run("run-001")
            state = store.read_state("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"

            self.assertEqual(result.status, "RUNNING")
            self.assertEqual(state.retry_count["wp-r1_implement"], 1)
            self.assertIn("wp-r1_implement", state.current_nodes)
            self.assertNotIn("wp-r1_implement", state.failed_nodes)
            self.assertIn("spec_binding_node", state.completed_nodes)
            events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            event_types = [event["type"] for event in events]
            self.assertIn("node_failed", event_types)
            self.assertIn("node_invalidated", event_types)
            self.assertEqual(event_types[-1], "node_retry_scheduled")

    def test_executor_rejects_handler_success_with_disallowed_artifact_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")
            seen_context_paths: list[str] = []

            def bad_handler(run_id, graph, state, node_payload):
                seen_context_paths.append(node_payload["context_pack_path"])
                artifact_path = root / ".loop" / "runs" / run_id / "artifacts" / "wp-r1_implement.json"
                artifact_path.write_text(json.dumps({"status": "success"}), encoding="utf-8")
                return NodeExecutionResult(
                    status="success",
                    summary="bad success",
                    artifacts=[
                        {
                            "type": "review-result",
                            "path": f".loop/runs/{run_id}/artifacts/wp-r1_implement.json",
                            "description": "wrong artifact type",
                        }
                    ],
                )

            result = DRGExecutor(root, node_handlers={"wp-r1_implement": bad_handler}).run("run-001")
            state = store.read_state("run-001")

            self.assertEqual(result.status, "RUNNING")
            self.assertTrue(any(issue.code == "handler_result.artifact_type_not_allowed" for issue in result.issues))
            self.assertEqual(state.retry_count["wp-r1_implement"], 1)
            self.assertTrue(seen_context_paths)
            self.assertTrue((root / seen_context_paths[0]).is_file())

    def test_executor_escalates_when_retry_limit_is_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            def fail_node(run_id, graph, state, node_payload):
                return NodeExecutionResult(status="failed", summary="hard failure", error="hard failure")

            result = DRGExecutor(
                root,
                node_handlers={"wp-r1_implement": fail_node},
                retry_policy=RetryPolicy(max_retries=0),
            ).run("run-001")
            state = store.read_state("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"

            self.assertEqual(result.status, "ESCALATED")
            self.assertEqual(state.status, "ESCALATED")
            self.assertIn("wp-r1_implement", state.failed_nodes)
            self.assertEqual(state.last_error, "hard failure")
            events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["type"], "run_escalated")

    def test_retry_policy_can_escalate_by_node_type_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            def fail_node(run_id, graph, state, node_payload):
                return NodeExecutionResult(status="failed", summary="implementation failure", error="implementation failure")

            result = DRGExecutor(
                root,
                node_handlers={"wp-r1_implement": fail_node},
                retry_policy=RetryPolicy(node_type_max_retries={"implementation": 0}),
            ).run("run-001")
            state = store.read_state("run-001")
            events = [
                json.loads(line)
                for line in (root / ".loop" / "runs" / "run-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(result.status, "ESCALATED")
            self.assertEqual(state.status, "ESCALATED")
            self.assertEqual(events[-1]["type"], "run_escalated")
            self.assertEqual(events[-1]["max_retries"], 0)

    def test_retry_policy_node_limit_overrides_node_type_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            def fail_node(run_id, graph, state, node_payload):
                return NodeExecutionResult(status="failed", summary="implementation failure", error="implementation failure")

            result = DRGExecutor(
                root,
                node_handlers={"wp-r1_implement": fail_node},
                retry_policy=RetryPolicy(
                    node_max_retries={"wp-r1_implement": 2},
                    node_type_max_retries={"implementation": 0},
                ),
            ).run("run-001")
            state = store.read_state("run-001")
            events = [
                json.loads(line)
                for line in (root / ".loop" / "runs" / "run-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(result.status, "RUNNING")
            self.assertEqual(state.retry_count["wp-r1_implement"], 1)
            self.assertEqual(events[-1]["type"], "node_retry_scheduled")
            self.assertEqual(events[-1]["max_retries"], 2)

    def test_retry_policy_can_escalate_by_failure_reason_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            def fail_node(run_id, graph, state, node_payload):
                return NodeExecutionResult(status="failed", summary="tool permission denied", error="tool permission denied")

            result = DRGExecutor(
                root,
                node_handlers={"wp-r1_implement": fail_node},
                retry_policy=RetryPolicy(reason_max_retries={"permission denied": 0}),
            ).run("run-001")
            events = [
                json.loads(line)
                for line in (root / ".loop" / "runs" / "run-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(result.status, "ESCALATED")
            self.assertEqual(events[-1]["type"], "run_escalated")
            self.assertEqual(events[-1]["max_retries"], 0)

    def test_retry_policy_loads_from_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy_path = root / "retry-policy.json"
            policy_path.write_text(json.dumps({
                "default_max_retries": 4,
                "node_max_retries": {"final_gate_node": 1},
                "node_type_max_retries": {"test": 2},
                "reason_max_retries": {"permission denied": 0},
                "retryable_node_types": ["implementation", "test", "gate"],
            }), encoding="utf-8")

            policy = load_retry_policy(policy_path)

            self.assertEqual(policy.max_retries, 4)
            self.assertEqual(policy.max_retries_for("final_gate_node", "gate"), 1)
            self.assertEqual(policy.max_retries_for("wp-r1_test", "test"), 2)
            self.assertEqual(policy.max_retries_for("wp-r1_implement", "implementation", "tool permission denied"), 0)
            self.assertIn("gate", policy.retryable_node_types)
            self.assertNotIn("review", policy.retryable_node_types)

    def test_final_gate_runs_acceptance_check_and_records_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            result = DRGExecutor(root).run("run-001")
            gate = json.loads((root / ".loop" / "runs" / "run-001" / "artifacts" / "final_gate_node.json").read_text(encoding="utf-8"))

            self.assertEqual(result.status, "DONE")
            self.assertEqual(gate["verdict"], "PASS")
            self.assertEqual(gate["checks"][0]["id"], "a1")
            self.assertTrue(gate["checks"][0]["passed"])

    def test_final_gate_failure_schedules_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            def fail_gate(run_id, graph, state, node_payload):
                return NodeExecutionResult(status="failed", summary="final gate failure", error="final gate failure")

            result = DRGExecutor(root, node_handlers={"final_gate_node": fail_gate}).run("run-001")
            state = store.read_state("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"

            self.assertEqual(result.status, "RUNNING")
            self.assertEqual(state.retry_count["final_gate_node"], 1)
            self.assertIn("final_gate_node", state.current_nodes)
            self.assertFalse((run_dir / "artifacts" / "final_gate_node.json").exists())
            events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            event_types = [event["type"] for event in events]
            self.assertIn("node_failed", event_types)
            self.assertEqual(event_types[-1], "node_retry_scheduled")

    def test_test_agent_runs_scoped_acceptance_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            result = DRGExecutor(root).run("run-001")
            test_result = json.loads((root / ".loop" / "runs" / "run-001" / "artifacts" / "wp-r1_test.json").read_text(encoding="utf-8"))

            self.assertEqual(result.status, "DONE")
            self.assertEqual(test_result["verdict"], "PASS")
            self.assertEqual(test_result["checks"][0]["id"], "a1")
            self.assertTrue(test_result["checks"][0]["passed"])

    def test_test_agent_failure_schedules_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf nope", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            result = DRGExecutor(root).run("run-001")
            state = store.read_state("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"

            self.assertEqual(result.status, "RUNNING")
            self.assertEqual(state.retry_count["wp-r1_test"], 1)
            self.assertIn("wp-r1_test", state.current_nodes)
            self.assertFalse((run_dir / "artifacts" / "wp-r1_test.json").exists())


    def test_intent_audit_reports_no_intent_docs_when_documentation_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            GraphRunStore(root).create(snapshot_path, run_id="run-001")

            audit = run_intent_audit(root, "run-001")

            self.assertEqual(audit.status, NO_INTENT_DOCS)
            self.assertIsNone(audit.doc_source)
            self.assertEqual(audit.findings, [])

    def test_intent_audit_flags_incomplete_when_core_doc_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            GraphRunStore(root).create(snapshot_path, run_id="run-001")
            doc_dir = root / ".loop" / "runs" / "run-001" / "documentation"
            doc_dir.mkdir(parents=True)
            (doc_dir / "architecture.md").write_text("# arch", encoding="utf-8")

            audit = run_intent_audit(root, "run-001")

            self.assertEqual(audit.status, INTENT_INCOMPLETE)
            self.assertEqual(audit.doc_source, "run")
            self.assertIn("architecture.md", audit.present_docs)
            self.assertIn("permissions.md", audit.missing_core_docs)
            self.assertTrue(len(audit.findings) >= 4)

    def test_intent_audit_passes_when_all_core_docs_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            GraphRunStore(root).create(snapshot_path, run_id="run-001")
            doc_dir = root / ".loop" / "runs" / "run-001" / "documentation"
            doc_dir.mkdir(parents=True)
            from scheduler.intent_audit import CORE_INTENT_DOCS
            for name in CORE_INTENT_DOCS:
                (doc_dir / name).write_text(f"# {name}", encoding="utf-8")

            audit = run_intent_audit(root, "run-001")

            self.assertEqual(audit.status, INTENT_PRESENT)
            self.assertEqual(audit.findings, [])

    def test_conformance_report_gap_found_when_intent_docs_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            GraphRunStore(root).create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            graph = compile_execution_graph(root, "run-001")
            doc_dir = root / ".loop" / "runs" / "run-001" / "documentation"
            doc_dir.mkdir(parents=True)
            (doc_dir / "architecture.md").write_text("# arch only", encoding="utf-8")

            store = GraphRunStore(root)
            state = store.read_state("run-001")
            state.status = "RUNNING"
            state.completed_nodes = [n["id"] for n in graph.payload["nodes"]]
            store.write_state(state)
            for node in graph.payload["nodes"]:
                artifact_path = root / ".loop" / "runs" / "run-001" / "artifacts" / f"{node['id']}.json"
                artifact_path.write_text(json.dumps({"status": "success"}), encoding="utf-8")

            report = generate_conformance_report(root, "run-001", graph.payload, state.completed_nodes)

            self.assertEqual(report.issues, [])
            self.assertEqual(report.payload["overall"], "GAP_FOUND")
            self.assertEqual(report.payload["intent_audit"]["status"], "INTENT_INCOMPLETE")
            self.assertTrue(report.payload["intent_audit"]["findings"])
            self.assertTrue(validate_conformance_report_payload(report.payload).ok)

    def test_conformance_gap_found_proceeds_to_review_not_spec_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")
            doc_dir = root / ".loop" / "runs" / "run-001" / "documentation"
            doc_dir.mkdir(parents=True)
            (doc_dir / "architecture.md").write_text("# arch only", encoding="utf-8")

            result = DRGExecutor(root).run("run-001")
            state = store.read_state("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"

            # GAP_FOUND proceeds to delivery review (DONE), not AWAITING_SPEC_CHANGE
            self.assertEqual(result.status, "DONE")
            self.assertEqual(state.status, "DONE")
            report = json.loads((run_dir / "conformance-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["overall"], "GAP_FOUND")
            self.assertFalse((run_dir / "spec-change-request.json").exists())

    def test_conformance_no_intent_docs_stays_pass_in_full_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            GraphRunStore(root).create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            result = DRGExecutor(root).run("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"

            self.assertEqual(result.status, "DONE")
            report = json.loads((run_dir / "conformance-report.json").read_text(encoding="utf-8"))
            # No documentation dir -> NO_INTENT_DOCS -> overall stays PASS
            self.assertEqual(report["overall"], "PASS")
            self.assertEqual(report["intent_audit"]["status"], "NO_INTENT_DOCS")

    # --- P0: SpecChange resume path (漏洞3) ---

    def test_resume_run_from_spec_change_rebinds_and_completes(self) -> None:
        """AWAITING_SPEC_CHANGE → resume-run with v2 → DONE."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v1_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(v1_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            graph = compile_execution_graph(root, "run-001")
            state = store.read_state("run-001")
            state.status = "RUNNING"
            state.completed_nodes = [
                node["id"]
                for node in graph.payload["nodes"]
                if node["id"] not in {"conformance_node", "delivery_review_node", "deploy_node", "done_node"}
            ]
            store.write_state(state)

            DRGExecutor(root).run("run-001", max_steps=1)
            state = store.read_state("run-001")
            self.assertEqual(state.status, "AWAITING_SPEC_CHANGE")

            # Publish v2 with passing acceptance (check/expect filled)
            v2_payload = spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"})
            v2_payload["version"] = 2
            v2_payload["content_hash"] = calculate_content_hash(v2_payload)
            v2_path = write_snapshot(root, v2_payload)

            result = DRGExecutor(root).resume_spec_change("run-001", v2_path)
            state = store.read_state("run-001")

            self.assertEqual(result.status, "DONE")
            self.assertEqual(state.status, "DONE")
            self.assertEqual(state.bound_spec.version, 2)
            binding = json.loads(
                (root / ".loop" / "runs" / "run-001" / "spec-binding.json").read_text(encoding="utf-8")
            )
            self.assertEqual(binding["bound_spec"]["version"], 2)
            self.assertIn("rebound_at", binding)
            events = [
                json.loads(line)
                for line in (root / ".loop" / "runs" / "run-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            event_types = [e["type"] for e in events]
            self.assertIn("spec_rebound", event_types)
            self.assertIn("run_resumed_spec_change", event_types)
            self.assertIn("run_done", event_types)

    def test_resume_run_rejects_non_awaiting_status(self) -> None:
        """resume_spec_change on a DONE run → error."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")
            DRGExecutor(root).run("run-001")
            state = store.read_state("run-001")
            self.assertEqual(state.status, "DONE")

            v2_payload = spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"})
            v2_payload["version"] = 2
            v2_payload["content_hash"] = calculate_content_hash(v2_payload)
            v2_path = write_snapshot(root, v2_payload)

            result = DRGExecutor(root).resume_spec_change("run-001", v2_path)
            self.assertTrue(any(i.code == "executor.resume_spec_change.invalid_status" for i in result.issues))

    # --- P0: L3 human review pause/resume (漏洞4) ---

    def test_l3_review_handler_triggers_awaiting_human(self) -> None:
        """A review agent handler returning l3_review → AWAITING_HUMAN."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            def l3_review_handler(run_id, graph, state, node_payload):
                return NodeExecutionResult(
                    status="l3_review",
                    summary="L3 review required: intent gap detected",
                    error="intent-implementation gap requires human judgment",
                    artifacts=[],
                )

            result = DRGExecutor(
                root,
                agent_handlers={"review-agent": l3_review_handler},
            ).run("run-001")
            state = store.read_state("run-001")

            self.assertEqual(result.status, "AWAITING_HUMAN")
            self.assertEqual(state.status, "AWAITING_HUMAN")
            self.assertIn("delivery_review_node", state.blocked_nodes)
            self.assertNotIn("delivery_review_node", state.completed_nodes)
            events = [
                json.loads(line)
                for line in (root / ".loop" / "runs" / "run-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[-1]["type"], "l3_review_required")

    def test_resume_human_review_approve_completes_run(self) -> None:
        """AWAITING_HUMAN → resume-human-review APPROVE → DONE."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            def l3_review_handler(run_id, graph, state, node_payload):
                return NodeExecutionResult(
                    status="l3_review",
                    summary="L3 review required",
                    error="intent gap",
                    artifacts=[],
                )

            DRGExecutor(
                root,
                agent_handlers={"review-agent": l3_review_handler},
            ).run("run-001")
            state = store.read_state("run-001")
            self.assertEqual(state.status, "AWAITING_HUMAN")

            result = DRGExecutor(root).resume_human_review("run-001", "APPROVE", "gap is acceptable")
            state = store.read_state("run-001")

            self.assertEqual(result.status, "DONE")
            self.assertEqual(state.status, "DONE")
            self.assertIn("delivery_review_node", state.completed_nodes)
            events = [
                json.loads(line)
                for line in (root / ".loop" / "runs" / "run-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            event_types = [e["type"] for e in events]
            self.assertIn("l3_review_approved", event_types)
            self.assertIn("run_done", event_types)

    def test_resume_human_review_reject_escalates(self) -> None:
        """AWAITING_HUMAN → resume-human-review REJECT → ESCALATED."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            def l3_review_handler(run_id, graph, state, node_payload):
                return NodeExecutionResult(
                    status="l3_review",
                    summary="L3 review required",
                    error="intent gap",
                    artifacts=[],
                )

            DRGExecutor(
                root,
                agent_handlers={"review-agent": l3_review_handler},
            ).run("run-001")
            state = store.read_state("run-001")
            self.assertEqual(state.status, "AWAITING_HUMAN")

            result = DRGExecutor(root).resume_human_review("run-001", "REJECT", "unacceptable gap")
            state = store.read_state("run-001")

            self.assertEqual(result.status, "ESCALATED")
            self.assertEqual(state.status, "ESCALATED")
            self.assertIn("delivery_review_node", state.failed_nodes)
            events = [
                json.loads(line)
                for line in (root / ".loop" / "runs" / "run-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[-1]["type"], "l3_review_rejected")

    def test_resume_human_review_rejects_non_awaiting_status(self) -> None:
        """resume_human_review on a DONE run → error."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")
            DRGExecutor(root).run("run-001")

            result = DRGExecutor(root).resume_human_review("run-001", "APPROVE", "")
            self.assertTrue(any(i.code == "executor.resume_human_review.invalid_status" for i in result.issues))

    # --- P1: Run status transition table (漏洞1) ---

    def test_run_legal_transitions_match_executor_behavior(self) -> None:
        """RUN_LEGAL_TRANSITIONS covers all executor-driven transitions."""
        from scheduler.runs import RUN_LEGAL_TRANSITIONS, is_legal_run_transition

        # Core transitions from executor.run() + resume methods
        self.assertTrue(is_legal_run_transition("BOUND", "RUNNING"))
        self.assertTrue(is_legal_run_transition("RUNNING", "DONE"))
        self.assertTrue(is_legal_run_transition("RUNNING", "AWAITING_HUMAN"))
        self.assertTrue(is_legal_run_transition("RUNNING", "AWAITING_SPEC_CHANGE"))
        self.assertTrue(is_legal_run_transition("RUNNING", "ESCALATED"))
        self.assertTrue(is_legal_run_transition("AWAITING_HUMAN", "RUNNING"))
        self.assertTrue(is_legal_run_transition("AWAITING_HUMAN", "ESCALATED"))
        self.assertTrue(is_legal_run_transition("AWAITING_SPEC_CHANGE", "BOUND"))

        # Illegal transitions
        self.assertFalse(is_legal_run_transition("DONE", "RUNNING"))
        self.assertFalse(is_legal_run_transition("BOUND", "DONE"))
        self.assertFalse(is_legal_run_transition("ESCALATED", "RUNNING"))

        # Terminal states have no outgoing transitions
        for terminal in ("DONE", "FAILED", "ESCALATED", "SUPERSEDED"):
            self.assertEqual(RUN_LEGAL_TRANSITIONS[terminal], set())

    # --- P1: Gate checks (漏洞5) ---

    def test_plan_gate_checks_execution_plan_exists(self) -> None:
        """plan_gate_node verifies execution-plan.json exists with work_packages."""
        from scheduler.gate_auto import run_gate_checks

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            state = store.read_state("run-001")

            checks = run_gate_checks(root, state, "plan_gate_node")
            self.assertEqual(len(checks), 1)
            self.assertTrue(checks[0].passed)
            self.assertIn("work_packages", checks[0].actual)

    def test_plan_gate_fails_without_execution_plan(self) -> None:
        """plan_gate_node fails when execution-plan.json is missing."""
        from scheduler.gate_auto import run_gate_checks

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            state = store.read_state("run-001")

            checks = run_gate_checks(root, state, "plan_gate_node")
            self.assertEqual(len(checks), 1)
            self.assertFalse(checks[0].passed)

    def test_architecture_gate_checks_artifact_exists(self) -> None:
        """architecture_gate_node verifies architecture_impact_node.json exists."""
        from scheduler.gate_auto import run_gate_checks

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")
            state = store.read_state("run-001")

            # Pre-complete architecture_impact_node so its artifact exists
            arch_path = root / ".loop" / "runs" / "run-001" / "artifacts" / "architecture_impact_node.json"
            arch_path.write_text(json.dumps({
                "run_id": "run-001",
                "node_id": "architecture_impact_node",
                "status": "success",
            }), encoding="utf-8")

            checks = run_gate_checks(root, state, "architecture_gate_node")
            self.assertEqual(len(checks), 1)
            self.assertTrue(checks[0].passed)

    # --- P2: Wall-clock timeout (漏洞7) ---

    def test_wall_clock_timeout_fails_run(self) -> None:
        """Executor with wall_clock_timeout_seconds=0 → FAILED immediately."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            result = DRGExecutor(root, wall_clock_timeout_seconds=0.0).run("run-001")
            state = store.read_state("run-001")

            self.assertEqual(result.status, "FAILED")
            self.assertEqual(state.status, "FAILED")
            self.assertIn("wall-clock timeout", state.last_error or "")
            events = [
                json.loads(line)
                for line in (root / ".loop" / "runs" / "run-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[-1]["type"], "run_timeout")

    # --- P2: Run health checks (V-8/V-9/V-10/V-11) ---

    def test_spec_rebound_circuit_breaker_blocks_excess_rebind(self) -> None:
        """V-8: 3 spec_rebound events in events.jsonl → resume_spec_change returns FAILED."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v1_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(v1_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            graph = compile_execution_graph(root, "run-001")
            state = store.read_state("run-001")
            state.status = "RUNNING"
            state.completed_nodes = [
                node["id"]
                for node in graph.payload["nodes"]
                if node["id"] not in {"conformance_node", "delivery_review_node", "deploy_node", "done_node"}
            ]
            store.write_state(state)
            DRGExecutor(root).run("run-001", max_steps=1)
            state = store.read_state("run-001")
            self.assertEqual(state.status, "AWAITING_SPEC_CHANGE")

            # Manually append 3 spec_rebound events to simulate 3 prior rebinds
            events_path = root / ".loop" / "runs" / "run-001" / "events.jsonl"
            for i in range(3):
                events_path.open("a", encoding="utf-8").write(
                    json.dumps({"timestamp": f"2026-09-03T10:00:0{i}+00:00", "type": "spec_rebound", "run_id": "run-001"}) + "\n"
                )

            v2_payload = spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"})
            v2_payload["version"] = 2
            v2_payload["content_hash"] = calculate_content_hash(v2_payload)
            v2_path = write_snapshot(root, v2_payload)

            result = DRGExecutor(root).resume_spec_change("run-001", v2_path)
            self.assertEqual(result.status, "FAILED")
            state = store.read_state("run-001")
            self.assertEqual(state.status, "FAILED")
            self.assertIn("spec rebound", state.last_error or "")

    def test_cumulative_wall_clock_timeout_fails_run(self) -> None:
        """V-9: events spanning > timeout → FAILED via cumulative check."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            # Overwrite events.jsonl with past timestamps including a
            # node_started event (needed for V-9 has_prior_execution gate)
            events_path = root / ".loop" / "runs" / "run-001" / "events.jsonl"
            events_path.write_text(
                json.dumps({"timestamp": "2020-01-01T00:00:00+00:00", "type": "run_created", "run_id": "run-001"}) + "\n"
                + json.dumps({"timestamp": "2020-01-01T00:00:10+00:00", "type": "node_started", "run_id": "run-001", "node_id": "spec_binding_node"}) + "\n",
                encoding="utf-8",
            )

            result = DRGExecutor(root, wall_clock_timeout_seconds=1.0).run("run-001")
            self.assertEqual(result.status, "FAILED")
            state = store.read_state("run-001")
            self.assertEqual(state.status, "FAILED")
            self.assertIn("cumulative wall-clock timeout", state.last_error or "")
            events = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[-1]["type"], "run_health_check_failed")

    def test_stuck_node_detected_by_health_check(self) -> None:
        """V-10 (secondary): node_started without terminal event → FAILED."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            # Append a node_started event without a matching terminal event
            events_path = root / ".loop" / "runs" / "run-001" / "events.jsonl"
            events_path.open("a", encoding="utf-8").write(
                json.dumps({"timestamp": "2026-09-03T10:00:00+00:00", "type": "node_started", "run_id": "run-001", "node_id": "spec_binding_node"}) + "\n"
            )

            result = DRGExecutor(root).run("run-001")
            self.assertEqual(result.status, "FAILED")
            state = store.read_state("run-001")
            self.assertEqual(state.status, "FAILED")
            self.assertIn("stuck node", state.last_error or "")

    def test_execute_node_exception_routes_through_retry(self) -> None:
        """V-10 (primary): handler throws → caught by try/except → handle_failure."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            def throwing_handler(run_id, graph, state, node_payload):
                raise RuntimeError("handler exploded")

            result = DRGExecutor(
                root,
                node_handlers={"spec_binding_node": throwing_handler},
            ).run("run-001")

            # Exception was caught, handle_failure was called → retry scheduled
            self.assertNotEqual(result.status, "DONE")
            state = store.read_state("run-001")
            # After retry, status should be RUNNING (retry scheduled) or ESCALATED
            self.assertIn(state.status, {"RUNNING", "ESCALATED"})
            events_path = root / ".loop" / "runs" / "run-001" / "events.jsonl"
            events = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
            ]
            event_types = [e["type"] for e in events]
            self.assertIn("node_failed", event_types)

    def test_cli_execute_continues_after_retry(self) -> None:
        """V-11: CLI execute while-loop resumes after retry-scheduled return."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            call_count = {"n": 0}

            def fail_once_then_succeed(run_id, graph, state, node_payload):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return NodeExecutionResult(
                        status="failed", summary="transient error", error="transient"
                    )
                # Write the artifact (reset_node deletes it during retry)
                artifact_path = root / ".loop" / "runs" / run_id / "artifacts" / "spec_binding_node.json"
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                artifact_path.write_text(
                    json.dumps({"status": "success", "node_id": "spec_binding_node"}), encoding="utf-8"
                )
                return NodeExecutionResult(
                    status="success",
                    summary="ok",
                    artifacts=[
                        {
                            "type": "node-result",
                            "path": f".loop/runs/{run_id}/artifacts/spec_binding_node.json",
                            "description": "result",
                        }
                    ],
                )

            executor = DRGExecutor(
                root,
                node_handlers={"spec_binding_node": fail_once_then_succeed},
            )
            result = executor.run("run-001")
            # First run() returns RUNNING (retry was scheduled)
            self.assertEqual(result.status, "RUNNING")

            # CLI while-loop: call run() again to continue
            result = executor.run("run-001")
            self.assertEqual(result.status, "DONE")

    # --- P2: done_node terminal marker (漏洞6) ---

    def test_done_node_writes_terminal_marker_artifact(self) -> None:
        """done_node artifact has terminal_marker=True; DONE logic is in post-loop check."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            DRGExecutor(root).run("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"
            done_artifact = json.loads((run_dir / "artifacts" / "done_node.json").read_text(encoding="utf-8"))

            self.assertTrue(done_artifact["terminal_marker"])
            self.assertEqual(done_artifact["status"], "success")
            state = store.read_state("run-001")
            self.assertEqual(state.status, "DONE")


class TestHuaDesignContract(unittest.TestCase):
    """huaDesign token 引用契约校验（方案 C 确定性层）。

    验证 validate_ui_design_contract 在声明 design_system=huaDesign 时强制：
    单一主色=color.primary.6、colors 值为 token dot-path、spacing 引用 4n token；
    未声明时跳过（向后兼容，不破坏现有 55 测试）。
    """

    def _base_payload(self, platform: str = "web") -> dict:
        return {
            "agent": "ui-design-agent",
            "task_id": "task-001",
            "round": 1,
            "status": "success",
            "artifacts": [],
            "summary": "x",
            "ui_design": {
                "platform": platform,
                "accessibility": ["键盘导航", "ARIA", "对比度"],
            },
        }

    def _web_payload(self, design_tokens: dict) -> dict:
        payload = self._base_payload("web")
        payload["ui_design"]["pages"] = [{
            "name": "Home", "route": "/", "layout": "x",
            "components": ["Header"], "states": ["loading", "error", "empty", "success"],
            "interactions": [],
        }]
        payload["ui_design"]["component_inventory"] = [{"name": "Header", "type": "reusable", "props": {}}]
        payload["ui_design"]["design_tokens"] = design_tokens
        return payload

    def _qt_payload(self, qss_tokens: dict) -> dict:
        payload = self._base_payload("qt")
        payload["ui_design"]["windows"] = [{
            "name": "MainWindow", "class": "QMainWindow", "layout": "QVBoxLayout",
            "widgets": ["QMenuBar"], "states": ["idle", "submitting", "error"],
        }]
        payload["ui_design"]["widget_inventory"] = [{"name": "UserTable", "base_class": "QTableView", "type": "reusable", "properties": {}, "signals": []}]
        payload["ui_design"]["signals_slots"] = [{"signal": "x", "slot": "y"}]
        payload["ui_design"]["qss_tokens"] = qss_tokens
        return payload

    def test_no_design_system_skips_huadesign_check(self) -> None:
        """未声明 design_system 时不触发 huaDesign 契约校验（向后兼容）。"""
        payload = self._web_payload({"colors": {"primary": "#3B82F6"}})  # 裸值，但未声明 huaDesign
        result = validate_ui_design_contract(payload)
        hua_codes = [i.code for i in result.issues if i.code.startswith("ui_design.hua_")]
        self.assertEqual(hua_codes, [])

    def test_huadesign_primary_must_be_primary6(self) -> None:
        """声明 huaDesign 后 primary 必须为 color.primary.6（单一主色）。"""
        payload = self._web_payload({
            "design_system": "huaDesign",
            "tokens_ref": "dahua-direction/UI/tokens/hua_design_tokens.json",
            "colors": {"primary": "#3B82F6"},  # 裸 Tailwind blue，违反单一主色
        })
        result = validate_ui_design_contract(payload)
        codes = [i.code for i in result.issues]
        self.assertIn("ui_design.hua_invalid_primary", codes)
        self.assertIn("ui_design.hua_bare_color_value", codes)

    def test_huadesign_valid_token_refs_pass(self) -> None:
        """声明 huaDesign 且 colors 全为合法 token 引用、primary=color.primary.6 → 通过。"""
        payload = self._web_payload({
            "design_system": "huaDesign",
            "tokens_ref": "dahua-direction/UI/tokens/hua_design_tokens.json",
            "colors": {
                "primary": "color.primary.6",
                "background": "color.neutral.bg",
                "surface": "color.neutral.bgCard",
                "text_primary": "color.neutral.fontR",
                "error": "color.error.6",
            },
            "spacing": {"grid_unit": "spacing.x1", "scale": ["spacing.x1", "spacing.x4", "spacing.x16"]},
        })
        result = validate_ui_design_contract(payload)
        hua_issues = [i for i in result.issues if i.code.startswith("ui_design.hua_")]
        self.assertEqual(hua_issues, [])

    def test_huadesign_bare_spacing_rejected(self) -> None:
        """声明 huaDesign 后 spacing.scale 含裸值 → 报错。"""
        payload = self._web_payload({
            "design_system": "huaDesign",
            "tokens_ref": "dahua-direction/UI/tokens/hua_design_tokens.json",
            "colors": {"primary": "color.primary.6"},
            "spacing": {"grid_unit": "spacing.x1", "scale": ["spacing.x4", "16px"]},  # 16px 裸值
        })
        result = validate_ui_design_contract(payload)
        codes = [i.code for i in result.issues]
        self.assertIn("ui_design.hua_bare_spacing", codes)

    def test_huadesign_missing_tokens_ref_rejected(self) -> None:
        """声明 huaDesign 但缺 tokens_ref → 报错。"""
        payload = self._web_payload({
            "design_system": "huaDesign",
            "colors": {"primary": "color.primary.6"},
        })
        result = validate_ui_design_contract(payload)
        codes = [i.code for i in result.issues]
        self.assertIn("ui_design.hua_missing_tokens_ref", codes)

    def test_huadesign_qt_qss_tokens_contract(self) -> None:
        """QT 模式 qss_tokens 声明 huaDesign 同样校验 token 引用。"""
        payload = self._qt_payload({
            "design_system": "huaDesign",
            "tokens_ref": "dahua-direction/UI/tokens/hua_design_tokens.json",
            "colors": {"primary": "#1C79F4"},  # 裸值，违反
        })
        result = validate_ui_design_contract(payload)
        codes = [i.code for i in result.issues]
        self.assertIn("ui_design.hua_invalid_primary", codes)


class TestStrictSpecContract(unittest.TestCase):
    """1.1 严格 Requirement/Acceptance Schema 校验。

    验证 schema_version=1.1 时强制 EARS + GWT + 显式映射 + 类型条件；
    1.0 向后兼容不强制。
    """

    def _strict_requirement(self, **overrides) -> dict:
        req = {
            "id": "REQ-001",
            "type": "functional",
            "priority": "MUST",
            "actor": "end_user",
            "condition": "when the user opens the demo page",
            "system_response": "the system shall render the demo component",
            "source_refs": [],
            "tags": ["ui"],
        }
        req.update(overrides)
        return req

    def _strict_acceptance(self, **overrides) -> dict:
        acc = {
            "id": "ACC-001",
            "requirement_ids": ["REQ-001"],
            "type": "automated",
            "scenario": "demo renders on page load",
            "given": ["the demo page is loaded"],
            "when": ["the page finishes rendering"],
            "then": ["the demo component is visible"],
            "check": "printf ok",
            "expect": "ok",
            "evidence_required": ["test-result"],
        }
        acc.update(overrides)
        return acc

    def _strict_change_spec(self, **overrides) -> dict:
        payload = {
            "schema_version": "1.1",
            "spec_id": "task-001",
            "project": "web-app",
            "intent": {"summary": "Demo"},
            "scope": {"in": ["demo"], "out": []},
            "requirements": [self._strict_requirement()],
            "acceptance_criteria": [self._strict_acceptance()],
            "constraints": ["change constraint"],
            "dependencies": [],
            "assumptions": [],
            "open_questions": [],
            "product_shape": {"product_type": "web"},
            "source_refs": [],
        }
        payload.update(overrides)
        return payload

    def _strict_snapshot_payload(self, status: str = "PUBLISHED") -> dict:
        payload = {
            "schema_version": "1.1",
            "spec_id": "task-001",
            "version": 1,
            "status": status,
            "published_at": "2026-08-26T10:00:00+08:00",
            "content_hash": "",
            "project": "web-app",
            "intent": {"summary": "Demo"},
            "scope": {"in": ["demo"], "out": []},
            "requirements": [self._strict_requirement()],
            "acceptance_criteria": [self._strict_acceptance()],
            "constraints": [],
            "dependencies": [],
            "product_shape": {"product_type": "web"},
            "source_refs": [],
        }
        payload["content_hash"] = calculate_content_hash(payload)
        return payload

    def _write_strict_snapshot(self, root: Path, payload: dict | None = None) -> Path:
        selected = payload or self._strict_snapshot_payload()
        path = (
            root / "specs" / "web-app" / "published" / "task-001"
            / f"v{selected['version']}" / "spec-snapshot.json"
        )
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(selected, ensure_ascii=False), encoding="utf-8")
        return path

    # --- P0-1: Schema 结构校验 ---

    def test_strict_1_1_valid_spec_passes_validation(self) -> None:
        """完整 1.1 spec（EARS + GWT + 映射 + 类型条件）通过校验。"""
        result = validate_change_spec_payload(self._strict_change_spec())
        codes = [i.code for i in result.issues if i.severity in ("critical", "error")]
        self.assertEqual(codes, [], f"unexpected issues: {result.issues}")

    def test_strict_1_1_rejects_missing_ears_actor(self) -> None:
        """Requirement 缺 actor → error。"""
        spec = self._strict_change_spec()
        spec["requirements"][0].pop("actor")
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("requirement.missing_ears", codes)

    def test_strict_1_1_rejects_missing_ears_system_response(self) -> None:
        """Requirement 缺 system_response → error。"""
        spec = self._strict_change_spec()
        spec["requirements"][0].pop("system_response")
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("requirement.missing_ears", codes)

    def test_strict_1_1_rejects_invalid_requirement_type(self) -> None:
        """Requirement type 不合法 → error。"""
        spec = self._strict_change_spec()
        spec["requirements"][0]["type"] = "bogus"
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("requirement.invalid_type", codes)

    def test_strict_1_1_rejects_invalid_priority(self) -> None:
        """priority 不在 MUST/SHOULD/MAY → error。"""
        spec = self._strict_change_spec()
        spec["requirements"][0]["priority"] = "NEVER"
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("requirement.invalid_priority", codes)

    def test_strict_1_1_rejects_missing_gwt_given(self) -> None:
        """Acceptance 缺 given → error。"""
        spec = self._strict_change_spec()
        spec["acceptance_criteria"][0].pop("given")
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("acceptance.missing_gwt", codes)

    def test_strict_1_1_rejects_missing_evidence_required(self) -> None:
        """Acceptance 缺 evidence_required → error。"""
        spec = self._strict_change_spec()
        spec["acceptance_criteria"][0].pop("evidence_required")
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("acceptance.missing_evidence_required", codes)

    # --- P0-2: 显式映射校验 ---

    def test_strict_1_1_rejects_orphan_requirement(self) -> None:
        """Requirement 无任何 Acceptance 覆盖 → critical。"""
        spec = self._strict_change_spec()
        spec["requirements"].append(self._strict_requirement(id="REQ-002"))
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("requirement.orphan_no_acceptance", codes)

    def test_strict_1_1_rejects_acceptance_referencing_unknown_requirement(self) -> None:
        """Acceptance.requirement_ids 引用不存在的 Requirement → critical。"""
        spec = self._strict_change_spec()
        spec["acceptance_criteria"][0]["requirement_ids"] = ["REQ-999"]
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("acceptance.invalid_requirement_ref", codes)

    def test_strict_1_1_rejects_empty_requirement_ids(self) -> None:
        """Acceptance.requirement_ids 为空 → critical。"""
        spec = self._strict_change_spec()
        spec["acceptance_criteria"][0]["requirement_ids"] = []
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("acceptance.missing_requirement_ids", codes)

    def test_strict_1_1_rejects_duplicate_requirement_id(self) -> None:
        """Requirement ID 重复 → critical。"""
        spec = self._strict_change_spec()
        spec["requirements"].append(self._strict_requirement())
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("requirement.duplicate_id", codes)

    # --- P0-3: 类型条件校验 ---

    def test_strict_1_1_rejects_automated_without_check(self) -> None:
        """automated Acceptance 缺 check → critical。"""
        spec = self._strict_change_spec()
        spec["acceptance_criteria"][0].pop("check")
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("acceptance.automated_missing_check", codes)

    def test_strict_1_1_rejects_manual_without_reviewer_or_scope(self) -> None:
        """manual Acceptance 缺 reviewer + review_scope → critical。"""
        spec = self._strict_change_spec()
        spec["acceptance_criteria"][0].update({
            "type": "manual",
            "reviewer": None,
            "review_scope": None,
        })
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("acceptance.manual_missing_reviewer", codes)

    def test_strict_1_1_rejects_performance_without_metric(self) -> None:
        """performance Acceptance 缺 metric → critical。"""
        spec = self._strict_change_spec()
        spec["acceptance_criteria"][0].update({
            "type": "performance",
            "threshold": "< 200ms",
        })
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("acceptance.performance_missing_metric", codes)

    def test_strict_1_1_rejects_security_without_evidence(self) -> None:
        """security Acceptance 缺 threat+control/check/scope → critical。"""
        spec = self._strict_change_spec()
        spec["acceptance_criteria"][0].update({
            "type": "security",
            "check": None,
            "review_scope": None,
        })
        result = validate_change_spec_payload(spec)
        codes = [i.code for i in result.issues]
        self.assertIn("acceptance.security_missing_evidence", codes)

    def test_strict_1_1_manual_with_review_scope_passes(self) -> None:
        """manual Acceptance 有 review_scope → 通过。"""
        spec = self._strict_change_spec()
        spec["acceptance_criteria"][0].update({
            "type": "manual",
            "review_scope": "manual visual inspection of login page",
        })
        result = validate_change_spec_payload(spec)
        type_issues = [i for i in result.issues if i.code.startswith("acceptance.")]
        self.assertEqual(type_issues, [])

    # --- P0: 1.0 向后兼容 ---

    def test_strict_1_0_backward_compatible_no_ears_gwt(self) -> None:
        """1.0 spec 无 EARS/GWT/requirement_ids 仍通过校验。"""
        result = validate_change_spec_payload(change_spec_payload())
        severity_issues = [i for i in result.issues if i.severity in ("critical", "error")]
        # 1.0 may have open_questions or other issues, but should NOT have strict schema issues
        strict_codes = [i.code for i in result.issues if i.code.startswith(("requirement.", "acceptance."))]
        self.assertEqual(strict_codes, [])

    def test_strict_1_1_publishes_and_creates_immutable_snapshot(self) -> None:
        """1.1 ChangeSpec 发布为不可变 SpecSnapshot，schema_version 保留。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_change_spec(root, self._strict_change_spec())
            result = publish_change_spec(root, path, version=1)
            self.assertEqual(result.issues, [])
            assert result.path is not None
            snapshot = load_spec_snapshot(result.path)
            self.assertEqual(snapshot.issues, [])
            self.assertEqual(snapshot.payload["schema_version"], "1.1")
            self.assertEqual(snapshot.status, "PUBLISHED")

    # --- P0-4: ExecutionPlan 按 requirement_ids 分组 + WP 元数据 ---

    def test_strict_1_1_plan_groups_by_requirement_ids_and_has_metadata(self) -> None:
        """1.1 plan 按 requirement_ids 分组，WP 含 requirement_type/priority/tags/implementation_focus/planning_basis。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = self._write_strict_snapshot(root)
            GraphRunStore(root).create(snapshot_path, run_id="run-001")

            plan = generate_execution_plan(root, "run-001")

            self.assertEqual(plan.issues, [])
            wp = plan.payload["work_packages"][0]
            self.assertEqual(wp["id"], "wp-req-001")
            self.assertEqual(wp["requirements"], ["REQ-001"])
            self.assertEqual(wp["acceptance_criteria"], ["ACC-001"])
            self.assertEqual(wp["requirement_type"], "functional")
            self.assertEqual(wp["priority"], "MUST")
            self.assertEqual(wp["tags"], ["ui"])
            self.assertIn("render the demo component", wp["implementation_focus"])
            self.assertEqual(wp["planning_basis"], "requirement_ears")

    def test_strict_1_1_plan_multi_requirement_no_cross_assignment(self) -> None:
        """1.1 多 Requirement 多 Acceptance 不串配（requirement_ids 精确分组）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = self._strict_snapshot_payload()
            spec["requirements"] = [
                self._strict_requirement(id="REQ-A", system_response="shall do A"),
                self._strict_requirement(id="REQ-B", system_response="shall do B", tags=["data"]),
            ]
            spec["acceptance_criteria"] = [
                self._strict_acceptance(id="ACC-A1", requirement_ids=["REQ-A"]),
                self._strict_acceptance(id="ACC-B1", requirement_ids=["REQ-B"]),
            ]
            spec["content_hash"] = calculate_content_hash(spec)
            snapshot_path = self._write_strict_snapshot(root, spec)
            GraphRunStore(root).create(snapshot_path, run_id="run-001")

            plan = generate_execution_plan(root, "run-001")

            self.assertEqual(plan.issues, [])
            wps = {wp["id"]: wp for wp in plan.payload["work_packages"]}
            self.assertEqual(wps["wp-req-a"]["acceptance_criteria"], ["ACC-A1"])
            self.assertEqual(wps["wp-req-b"]["acceptance_criteria"], ["ACC-B1"])
            self.assertEqual(wps["wp-req-b"]["tags"], ["data"])

    # --- P0-5: Conformance Trace Matrix ---

    def test_strict_1_1_conformance_has_trace_matrix(self) -> None:
        """1.1 ConformanceReport 含 trace_matrix，结构正确。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = self._write_strict_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            graph = compile_execution_graph(root, "run-001")
            state = store.read_state("run-001")
            state.status = "RUNNING"
            state.completed_nodes = [n["id"] for n in graph.payload["nodes"]]
            store.write_state(state)
            for node in graph.payload["nodes"]:
                artifact_path = root / ".loop" / "runs" / "run-001" / "artifacts" / f"{node['id']}.json"
                artifact_path.write_text(json.dumps({"status": "success"}), encoding="utf-8")

            report = generate_conformance_report(root, "run-001", graph.payload, state.completed_nodes)

            self.assertEqual(report.issues, [])
            self.assertEqual(report.payload["overall"], "PASS")
            tm = report.payload["trace_matrix"]
            self.assertEqual(len(tm), 1)
            entry = tm[0]
            self.assertEqual(entry["requirement_id"], "REQ-001")
            self.assertEqual(entry["priority"], "MUST")
            self.assertIn("ACC-001", entry["acceptance_ids"])
            self.assertEqual(entry["status"], "PASS")
            self.assertTrue(entry["evidence_refs"])
            self.assertTrue(validate_conformance_report_payload(report.payload).ok)

    def test_strict_1_1_conformance_fail_for_orphan_requirement(self) -> None:
        """1.1 Conformance: requirement 无 acceptance 映射 → trace_matrix FAIL + overall FAIL。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = self._strict_snapshot_payload()
            spec["requirements"] = [
                self._strict_requirement(id="REQ-COVERED"),
                self._strict_requirement(id="REQ-ORPHAN"),
            ]
            spec["acceptance_criteria"] = [
                self._strict_acceptance(id="ACC-1", requirement_ids=["REQ-COVERED"]),
                self._strict_acceptance(id="ACC-2", requirement_ids=["REQ-ORPHAN"]),
            ]
            spec["content_hash"] = calculate_content_hash(spec)
            snapshot_path = self._write_strict_snapshot(root, spec)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            graph = compile_execution_graph(root, "run-001")
            state = store.read_state("run-001")
            state.status = "RUNNING"
            state.completed_nodes = [n["id"] for n in graph.payload["nodes"]]
            store.write_state(state)
            for node in graph.payload["nodes"]:
                artifact_path = root / ".loop" / "runs" / "run-001" / "artifacts" / f"{node['id']}.json"
                artifact_path.write_text(json.dumps({"status": "success"}), encoding="utf-8")

            # Simulate orphan: remove acceptance_coverage for REQ-ORPHAN's work package
            orphan_wp = next(
                c["work_package_id"] for c in graph.payload["requirement_coverage"]
                if c["requirement_id"] == "REQ-ORPHAN"
            )
            modified_graph = dict(graph.payload)
            modified_graph["acceptance_coverage"] = [
                c for c in modified_graph.get("acceptance_coverage", [])
                if c.get("work_package_id") != orphan_wp
            ]

            report = generate_conformance_report(root, "run-001", modified_graph, state.completed_nodes)

            tm = report.payload["trace_matrix"]
            orphan_entry = next(e for e in tm if e["requirement_id"] == "REQ-ORPHAN")
            self.assertEqual(orphan_entry["status"], "FAIL")
            self.assertEqual(report.payload["overall"], "FAIL")


class TestDesignSpec(unittest.TestCase):
    """Design Spec P0: design_spec_node + design_gate_node 确定性 fallback。

    验证 design-spec.json 自动生成、design gate 校验、EvidenceManifest 纳入，
    以及 opt-in 哲学（无 design-spec.json 时 gate 自动 PASS）。
    """

    def test_design_spec_node_generates_design_spec_json(self) -> None:
        """design_spec_node 确定性 fallback 生成 design-spec.json。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            DRGExecutor(root).run("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"

            design_spec_path = run_dir / "design-spec.json"
            self.assertTrue(design_spec_path.is_file())
            spec = json.loads(design_spec_path.read_text(encoding="utf-8"))
            self.assertEqual(spec["schema_version"], "1.0")
            self.assertEqual(spec["spec_id"], "task-001")
            self.assertEqual(spec["spec_version"], 1)
            self.assertEqual(spec["status"], "DRAFT")
            self.assertTrue(spec["source_snapshot"]["content_hash"])
            self.assertEqual(len(spec["traceability"]), 1)
            self.assertEqual(spec["traceability"][0]["requirement_id"], "r1")
            self.assertTrue(spec["traceability"][0]["design_refs"])
            self.assertEqual(spec["open_questions"], [])

    def test_design_gate_node_passes_with_auto_generated_spec(self) -> None:
        """design_gate_node 对自动生成的 design-spec.json 全部 PASS。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            DRGExecutor(root).run("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"
            gate = json.loads((run_dir / "artifacts" / "design_gate_node.json").read_text(encoding="utf-8"))
            self.assertEqual(gate["verdict"], "PASS")
            self.assertTrue(all(c["passed"] for c in gate["checks"]))

    def test_design_gate_passes_when_no_design_spec(self) -> None:
        """无 design-spec.json 时 design gate 自动 PASS（opt-in 哲学）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")

            checks = run_design_gate_checks(root, "run-001")
            self.assertEqual(checks, [])

    def test_design_gate_fails_on_open_questions(self) -> None:
        """design-spec.json 有 open_questions 时 gate FAIL。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            spec = generate_minimal_design_spec(root, "run-001")
            spec.payload["open_questions"] = ["Which DB to use?"]
            write_design_spec(root, "run-001", spec.payload)

            checks = run_design_gate_checks(root, "run-001")
            check = next(c for c in checks if c.id == "design_gate.open_questions_resolved")
            self.assertFalse(check.passed)

    def test_design_gate_fails_on_missing_design_refs(self) -> None:
        """traceability 中 requirement 无 design_refs 时 gate FAIL。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            spec = generate_minimal_design_spec(root, "run-001")
            spec.payload["traceability"][0]["design_refs"] = []
            write_design_spec(root, "run-001", spec.payload)

            checks = run_design_gate_checks(root, "run-001")
            check = next(c for c in checks if c.id == "design_gate.requirement_has_design_refs")
            self.assertFalse(check.passed)

    def test_design_gate_fails_on_hash_mismatch(self) -> None:
        """source_snapshot.content_hash 与 bound spec 不匹配时 gate FAIL。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            spec = generate_minimal_design_spec(root, "run-001")
            spec.payload["source_snapshot"]["content_hash"] = "sha256:bogus"
            write_design_spec(root, "run-001", spec.payload)

            checks = run_design_gate_checks(root, "run-001")
            check = next(c for c in checks if c.id == "design_gate.source_hash_matches")
            self.assertFalse(check.passed)

    def test_design_spec_in_evidence_manifest(self) -> None:
        """design-spec.json 出现在 EvidenceManifest.run_files 中。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            DRGExecutor(root).run("run-001")
            run_dir = root / ".loop" / "runs" / "run-001"
            manifest = json.loads((run_dir / "evidence-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(any("design-spec.json" in item["path"] for item in manifest["run_files"]))

    def test_full_run_with_design_spec_nodes_completes_done(self) -> None:
        """完整执行含 design_spec_node + design_gate_node 的 DRG 到 DONE。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            graph = compile_execution_graph(root, "run-001")

            result = DRGExecutor(root).run("run-001")
            state = store.read_state("run-001")

            self.assertEqual(result.status, "DONE")
            self.assertEqual(state.status, "DONE")
            self.assertIn("design_spec_node", state.completed_nodes)
            self.assertIn("design_gate_node", state.completed_nodes)
            self.assertEqual(len(state.completed_nodes), len(graph.payload["nodes"]))

    def test_validate_design_spec_payload_rejects_missing_fields(self) -> None:
        """validate_design_spec_payload 对缺失字段报错。"""
        result = validate_design_spec_payload({"schema_version": "1.0"})
        codes = [i.code for i in result.issues]
        self.assertIn("design_spec.missing_field", codes)

    def test_validate_design_spec_payload_rejects_unsupported_version(self) -> None:
        """validate_design_spec_payload 对不支持的 schema_version 报错。"""
        payload = {
            "schema_version": "2.0",
            "project": "x",
            "spec_id": "x",
            "spec_version": 1,
            "design_id": "x",
            "status": "DRAFT",
            "source_snapshot": {"spec_id": "x", "version": 1, "content_hash": "x"},
            "architecture": {},
            "traceability": [],
            "open_questions": [],
            "approval": {},
        }
        result = validate_design_spec_payload(payload)
        codes = [i.code for i in result.issues]
        self.assertIn("design_spec.unsupported_schema", codes)

    def test_strict_1_1_full_run_with_design_spec(self) -> None:
        """1.1 严格 spec 完整执行含 design spec 节点。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            strict = TestStrictSpecContract()
            snapshot_path = strict._write_strict_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            result = DRGExecutor(root).run("run-001")

            self.assertEqual(result.status, "DONE")
            run_dir = root / ".loop" / "runs" / "run-001"
            design_spec = json.loads((run_dir / "design-spec.json").read_text(encoding="utf-8"))
            self.assertEqual(len(design_spec["traceability"]), 1)
            self.assertEqual(design_spec["traceability"][0]["requirement_id"], "REQ-001")
            self.assertIn("ACC-001", design_spec["traceability"][0]["acceptance_ids"])
            gate = json.loads((run_dir / "artifacts" / "design_gate_node.json").read_text(encoding="utf-8"))
            self.assertEqual(gate["verdict"], "PASS")


class TestDesignSpecPlanRefine(unittest.TestCase):
    """P1: Design-Spec-Aware plan refinement.

    Verifies that drg_compile_node refines the execution plan from a rich
    design spec (api_contracts / data_model / extra boundaries), recompiles
    the graph, and the executor hot-reloads the new graph.  Minimal
    deterministic fallback design specs do NOT trigger refinement.
    """

    # --- is_rich_design_spec unit tests ---

    def test_is_rich_design_spec_false_for_minimal(self) -> None:
        """Minimal design spec (no api/data, 1:1 boundaries) -> not rich."""
        minimal = {
            "api_contracts": [],
            "data_model": [],
            "architecture": {"component_boundaries": [{"name": "C1"}]},
            "traceability": [{"requirement_id": "r1"}],
        }
        self.assertFalse(is_rich_design_spec(minimal))

    def test_is_rich_design_spec_true_for_api_contracts(self) -> None:
        """Has api_contracts -> rich."""
        payload = {
            "api_contracts": [{"name": "API"}],
            "data_model": [],
            "architecture": {"component_boundaries": [{"name": "C1"}]},
            "traceability": [{"requirement_id": "r1"}],
        }
        self.assertTrue(is_rich_design_spec(payload))

    def test_is_rich_design_spec_true_for_data_model(self) -> None:
        """Has data_model -> rich."""
        payload = {
            "api_contracts": [],
            "data_model": [{"name": "User"}],
            "architecture": {"component_boundaries": [{"name": "C1"}]},
            "traceability": [{"requirement_id": "r1"}],
        }
        self.assertTrue(is_rich_design_spec(payload))

    def test_is_rich_design_spec_true_for_extra_boundaries(self) -> None:
        """More boundaries than traceability entries -> rich."""
        payload = {
            "api_contracts": [],
            "data_model": [],
            "architecture": {"component_boundaries": [{"name": "C1"}, {"name": "C2"}]},
            "traceability": [{"requirement_id": "r1"}],
        }
        self.assertTrue(is_rich_design_spec(payload))

    # --- refine_plan_from_design_spec unit tests ---

    def test_refine_not_triggered_for_minimal_design_spec(self) -> None:
        """Minimal design spec -> refine returns (False, original plan)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            spec = generate_minimal_design_spec(root, "run-001")
            write_design_spec(root, "run-001", spec.payload)

            refined, plan = refine_plan_from_design_spec(root, "run-001")
            self.assertFalse(refined)
            wp_ids = [wp["id"] for wp in plan.work_packages]
            self.assertIn("wp-r1", wp_ids)

    def test_refine_triggered_with_api_contracts(self) -> None:
        """Rich design spec with api_contracts -> refined, has wp-api."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            spec = generate_minimal_design_spec(root, "run-001")
            spec.payload["api_contracts"] = [{
                "name": "Demo API",
                "method": "GET",
                "path": "/api/demo",
                "request": {},
                "response": {"data": "string"},
                "errors": [],
                "requirements": ["r1"],
            }]
            write_design_spec(root, "run-001", spec.payload)

            refined, plan = refine_plan_from_design_spec(root, "run-001")
            self.assertTrue(refined)
            wp_ids = [wp["id"] for wp in plan.work_packages]
            self.assertIn("wp-api", wp_ids)
            wp_api = next(wp for wp in plan.work_packages if wp["id"] == "wp-api")
            self.assertEqual(wp_api["planning_basis"], "design_spec")
            self.assertEqual(wp_api["agent"], "backend-dev")
            self.assertIn("r1", wp_api["requirements"])

    def test_refine_triggered_with_data_model(self) -> None:
        """Rich design spec with data_model -> refined, has wp-data."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            spec = generate_minimal_design_spec(root, "run-001")
            spec.payload["data_model"] = [{
                "name": "DemoModel",
                "fields": [{"name": "id", "type": "string"}],
                "requirements": ["r1"],
            }]
            write_design_spec(root, "run-001", spec.payload)

            refined, plan = refine_plan_from_design_spec(root, "run-001")
            self.assertTrue(refined)
            wp_ids = [wp["id"] for wp in plan.work_packages]
            self.assertIn("wp-data", wp_ids)

    def test_refine_preserves_requirement_coverage(self) -> None:
        """Refined plan covers all requirements."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            spec = generate_minimal_design_spec(root, "run-001")
            spec.payload["api_contracts"] = [{
                "name": "Demo API",
                "method": "GET",
                "path": "/api/demo",
                "request": {},
                "response": {},
                "errors": [],
                "requirements": ["r1"],
            }]
            write_design_spec(root, "run-001", spec.payload)

            refined, plan = refine_plan_from_design_spec(root, "run-001")
            self.assertTrue(refined)
            covered_reqs = {
                item["requirement_id"]
                for item in plan.payload["requirement_coverage"]
            }
            self.assertIn("r1", covered_reqs)

    def test_refine_preserves_acceptance_coverage(self) -> None:
        """Refined plan covers all acceptances."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            spec = generate_minimal_design_spec(root, "run-001")
            spec.payload["api_contracts"] = [{
                "name": "Demo API",
                "method": "GET",
                "path": "/api/demo",
                "request": {},
                "response": {},
                "errors": [],
                "requirements": ["r1"],
            }]
            write_design_spec(root, "run-001", spec.payload)

            refined, plan = refine_plan_from_design_spec(root, "run-001")
            self.assertTrue(refined)
            covered_accs = {
                item["acceptance_id"]
                for item in plan.payload["acceptance_coverage"]
            }
            self.assertIn("a1", covered_accs)

    def test_refine_with_strict_1_1_spec(self) -> None:
        """1.1 strict spec with rich design spec -> refined with metadata."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            strict = TestStrictSpecContract()
            snapshot_path = strict._write_strict_snapshot(root)
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            spec = generate_minimal_design_spec(root, "run-001")
            spec.payload["api_contracts"] = [{
                "name": "Demo API",
                "method": "POST",
                "path": "/api/demo",
                "request": {},
                "response": {},
                "errors": [],
                "requirements": ["REQ-001"],
            }]
            write_design_spec(root, "run-001", spec.payload)

            refined, plan = refine_plan_from_design_spec(root, "run-001")
            self.assertTrue(refined)
            wp_api = next(wp for wp in plan.work_packages if wp["id"] == "wp-api")
            self.assertEqual(wp_api["planning_basis"], "design_spec")
            self.assertIn("REQ-001", wp_api["requirements"])
            self.assertIn("requirement_type", wp_api)
            self.assertIn("priority", wp_api)

    # --- Integration tests (full executor run) ---

    def _rich_design_spec_handler(self, root: Path):
        """Create a node handler for design_spec_node that writes a rich design spec."""
        def handler(run_id, graph, state, node_payload):
            spec = generate_minimal_design_spec(root, run_id)
            spec.payload["api_contracts"] = [{
                "name": "Demo API",
                "method": "GET",
                "path": "/api/demo",
                "request": {},
                "response": {"data": "string"},
                "errors": [],
                "requirements": ["r1"],
            }]
            write_design_spec(root, run_id, spec.payload)
            run_dir = root / ".loop" / "runs" / run_id
            artifact_path = run_dir / "artifacts" / f"{node_payload['id']}.json"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(json.dumps({
                "run_id": run_id,
                "node_id": node_payload["id"],
                "node_type": node_payload["type"],
                "agent": node_payload.get("agent"),
                "status": "success",
                "completed_at": "2026-08-26T00:00:00Z",
                "summary": "Rich design spec generated",
            }, ensure_ascii=False), encoding="utf-8")
            return NodeExecutionResult(
                status="success",
                summary="Rich design spec generated",
                artifacts=[{
                    "type": "node-result",
                    "path": str(artifact_path.relative_to(root)).replace("\\", "/"),
                    "description": "Design spec result",
                }],
            )
        return handler

    def test_full_run_with_rich_design_spec_completes_done(self) -> None:
        """Full run with rich design spec -> plan refined, graph recompiled, DONE."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            executor = DRGExecutor(
                root,
                node_handlers={"design_spec_node": self._rich_design_spec_handler(root)},
            )
            result = executor.run("run-001")

            self.assertEqual(result.status, "DONE")
            run_dir = root / ".loop" / "runs" / "run-001"
            # Plan was refined
            plan = json.loads((run_dir / "execution-plan.json").read_text(encoding="utf-8"))
            wp_ids = [wp["id"] for wp in plan["work_packages"]]
            self.assertIn("wp-api", wp_ids)
            # Graph was recompiled with new WP nodes
            graph = json.loads((run_dir / "execution-graph.json").read_text(encoding="utf-8"))
            graph_node_ids = {n["id"] for n in graph["nodes"]}
            self.assertIn("wp-api_implement", graph_node_ids)
            self.assertIn("wp-api_test", graph_node_ids)
            # drg_compile_node artifact records refinement
            compile_artifact = json.loads(
                (run_dir / "artifacts" / "drg_compile_node.json").read_text(encoding="utf-8")
            )
            self.assertTrue(compile_artifact["plan_refined"])

    def test_full_run_with_minimal_design_spec_no_refinement(self) -> None:
        """Full run with minimal (fallback) design spec -> no refinement, DONE."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = write_snapshot(
                root,
                spec_snapshot_payload(acceptance_check={"check": "printf ok", "expect": "ok"}),
            )
            store = GraphRunStore(root)
            store.create(snapshot_path, run_id="run-001")
            generate_execution_plan(root, "run-001")
            compile_execution_graph(root, "run-001")

            result = DRGExecutor(root).run("run-001")
            self.assertEqual(result.status, "DONE")
            run_dir = root / ".loop" / "runs" / "run-001"
            # Plan NOT refined
            plan = json.loads((run_dir / "execution-plan.json").read_text(encoding="utf-8"))
            wp_ids = [wp["id"] for wp in plan["work_packages"]]
            self.assertIn("wp-r1", wp_ids)
            self.assertNotIn("wp-api", wp_ids)
            # drg_compile_node artifact records no refinement
            compile_artifact = json.loads(
                (run_dir / "artifacts" / "drg_compile_node.json").read_text(encoding="utf-8")
            )
            self.assertFalse(compile_artifact["plan_refined"])


if __name__ == "__main__":
    unittest.main()
