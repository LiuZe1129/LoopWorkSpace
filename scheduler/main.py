from __future__ import annotations

import argparse
import json
from pathlib import Path

from .audit import audit_repository, format_audit
from .artifacts import ArtifactManager
from .drg import compile_execution_graph
from .evidence import generate_evidence_manifest
from .executor import DRGExecutor
from .events import EventLogger
from .graph import describe_graph
from .legacy import convert_task_card_to_change_spec
from .plans import generate_execution_plan
from .retry import RetryManager
from .retry_policy import load_retry_policy
from .runs import GraphRunStore
from .spec_flow import approve_revision_draft, create_revision_draft_from_scr, load_change_spec, publish_change_spec
from .specs import load_spec_snapshot
from .supersede import SupersedeManager
from .task_card import load_task_card
from .tool_contracts import generate_tool_contract_report
from .transition import TransitionValidator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Loop Engineer GraphFlow runtime prototype")
    parser.add_argument("--root", default=".", help="repository root")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("audit", help="audit task cards, artifacts, and DONE transition guards")
    sub.add_parser("graph", help="print the GraphFlow node and edge model")

    events = sub.add_parser("events", help="print jsonl events for a legacy task or GraphRun")
    events.add_argument("id")
    events.add_argument("--run", action="store_true", help="read .loop/runs/{run_id}/events.jsonl")

    transition = sub.add_parser("check-transition", help="validate a task-card status transition")
    transition.add_argument("task_card")
    transition.add_argument("target")

    validate_spec = sub.add_parser("validate-spec", help="validate a Published SpecSnapshot")
    validate_spec.add_argument("snapshot")

    validate_change = sub.add_parser("validate-change-spec", help="validate a ChangeSpec draft")
    validate_change.add_argument("change_spec")

    legacy_change = sub.add_parser("legacy-task-to-change-spec", help="convert a legacy task card to a ChangeSpec draft")
    legacy_change.add_argument("task_card")

    publish_spec = sub.add_parser("publish-spec", help="publish a ChangeSpec draft as an immutable SpecSnapshot")
    publish_spec.add_argument("change_spec")
    publish_spec.add_argument("--version", type=int, default=1)
    publish_spec.add_argument("--product-spec", default=None)

    smoke_flow = sub.add_parser("smoke-flow", help="run the full SpecSnapshot -> GraphRun smoke flow")
    smoke_flow.add_argument("change_spec")
    smoke_flow.add_argument("--version", type=int, default=1)
    smoke_flow.add_argument("--product-spec", default=None)
    smoke_flow.add_argument("--run-id", default=None)
    smoke_flow.add_argument("--retry-policy", default=None, help="optional retry policy JSON file")

    revise_spec = sub.add_parser("revise-spec-from-scr", help="create a ChangeSpec draft from a SpecChangeRequest")
    revise_spec.add_argument("spec_change_request")
    revise_spec.add_argument("base_snapshot")

    approve_revision = sub.add_parser("approve-revision-spec", help="approve a revision ChangeSpec draft for publish")
    approve_revision.add_argument("change_spec")
    approve_revision.add_argument("--approved-by", required=True)
    approve_revision.add_argument("--reason", default="")
    approve_revision.add_argument("--status", default="APPROVED", choices=["APPROVED", "REJECTED"])

    create_run = sub.add_parser("create-run", help="create a GraphRun bound to a Published SpecSnapshot")
    create_run.add_argument("snapshot")
    create_run.add_argument("--run-id", default=None)

    run_state = sub.add_parser("run-state", help="print and validate a GraphRun run-state.json")
    run_state.add_argument("run_id")

    plan = sub.add_parser("plan", help="generate execution-plan.json for a GraphRun")
    plan.add_argument("run_id")

    compile_graph = sub.add_parser("compile-graph", help="compile execution-graph.json for a GraphRun")
    compile_graph.add_argument("run_id")

    tool_contract = sub.add_parser("tool-contract", help="validate node tool contracts for a GraphRun")
    tool_contract.add_argument("run_id")

    evidence = sub.add_parser("evidence", help="generate evidence-manifest.json for a GraphRun")
    evidence.add_argument("run_id")

    execute = sub.add_parser("execute", help="execute ready nodes from execution-graph.json")
    execute.add_argument("run_id")
    execute.add_argument("--max-steps", type=int, default=None)
    execute.add_argument("--retry-policy", default=None, help="optional retry policy JSON file")

    reset_node = sub.add_parser("reset-node", help="invalidate a node and its downstream nodes for retry")
    reset_node.add_argument("run_id")
    reset_node.add_argument("node_id")

    supersede_run = sub.add_parser("supersede-run", help="mark an old GraphRun superseded and create a new GraphRun")
    supersede_run.add_argument("old_run_id")
    supersede_run.add_argument("new_snapshot")
    supersede_run.add_argument("--new-run-id", default=None)

    resume_run = sub.add_parser("resume-run", help="resume a GraphRun from AWAITING_SPEC_CHANGE with a new SpecSnapshot")
    resume_run.add_argument("run_id")
    resume_run.add_argument("new_snapshot")

    resume_human = sub.add_parser("resume-human-review", help="resume a GraphRun from AWAITING_HUMAN after L3 review")
    resume_human.add_argument("run_id")
    resume_human.add_argument("--decision", required=True, choices=["APPROVE", "REJECT"])
    resume_human.add_argument("--reason", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()

    if args.command == "audit":
        issues = audit_repository(root)
        print(format_audit(issues))
        return 1 if any(issue.severity in {"error", "critical"} for issue in issues) else 0

    if args.command == "graph":
        print(describe_graph())
        return 0

    if args.command == "events":
        logger = EventLogger(root)
        events = logger.read_run(args.id) if args.run else logger.read(args.id)
        if not events:
            print(f"No events found for {args.id}.")
            return 0
        for event in events:
            print(event)
        return 0

    if args.command == "check-transition":
        task = load_task_card(root / args.task_card)
        check = TransitionValidator(ArtifactManager(root)).validate(task, args.target)
        if check.allowed:
            print(f"Transition allowed: {check.current} -> {check.target}")
            if check.verified_artifacts:
                print("Verified artifacts: " + ", ".join(check.verified_artifacts))
            return 0
        print(f"Transition rejected: {check.current} -> {check.target}")
        for issue in check.issues:
            location = f" ({issue.path})" if issue.path else ""
            print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
        return 1

    if args.command == "validate-spec":
        snapshot = load_spec_snapshot((root / args.snapshot).resolve())
        if not snapshot.issues:
            print(
                "SpecSnapshot validation passed: "
                f"{snapshot.spec_id} v{snapshot.version} ({snapshot.content_hash})"
            )
            return 0
        print("SpecSnapshot validation failed:")
        for issue in snapshot.issues:
            location = f" ({issue.path})" if issue.path else ""
            print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
        return 1

    if args.command == "validate-change-spec":
        change = load_change_spec((root / args.change_spec).resolve())
        if not change.issues:
            print(f"ChangeSpec validation passed: {change.payload.get('spec_id')} ({change.payload.get('project')})")
            return 0
        print("ChangeSpec validation failed:")
        for issue in change.issues:
            location = f" ({issue.path})" if issue.path else ""
            print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
        return 1

    if args.command == "legacy-task-to-change-spec":
        result = convert_task_card_to_change_spec(root, (root / args.task_card).resolve())
        if result.issues:
            print("Legacy task conversion failed:")
            for issue in result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        assert result.path is not None
        print(json.dumps(
            {
                "change_spec": str(result.path.relative_to(root)),
                "spec_id": result.payload["spec_id"],
                "project": result.payload["project"],
                "source_task": args.task_card,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 0

    if args.command == "publish-spec":
        product_spec = (root / args.product_spec).resolve() if args.product_spec else None
        result = publish_change_spec(root, (root / args.change_spec).resolve(), version=args.version, product_spec_path=product_spec)
        if result.issues:
            print("Spec publish failed:")
            for issue in result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        assert result.path is not None
        print(json.dumps(
            {
                "snapshot": str(result.path.relative_to(root)),
                "spec_id": result.payload["spec_id"],
                "version": result.payload["version"],
                "content_hash": result.payload["content_hash"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 0

    if args.command == "smoke-flow":
        product_spec = (root / args.product_spec).resolve() if args.product_spec else None
        publish_result = publish_change_spec(
            root,
            (root / args.change_spec).resolve(),
            version=args.version,
            product_spec_path=product_spec,
        )
        if publish_result.issues:
            print("Smoke flow failed at publish-spec:")
            for issue in publish_result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        assert publish_result.path is not None

        state = GraphRunStore(root).create(publish_result.path, run_id=args.run_id)
        plan_result = generate_execution_plan(root, state.run_id)
        if plan_result.issues:
            print("Smoke flow failed at plan:")
            for issue in plan_result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        graph_result = compile_execution_graph(root, state.run_id)
        if graph_result.issues:
            print("Smoke flow failed at compile-graph:")
            for issue in graph_result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        contract_result = generate_tool_contract_report(root, state.run_id, graph_result.payload)
        if contract_result.issues:
            print("Smoke flow failed at tool-contract:")
            for issue in contract_result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        retry_policy = load_retry_policy((root / args.retry_policy).resolve()) if args.retry_policy else None
        execute_result = DRGExecutor(root, retry_policy=retry_policy).run(state.run_id)
        if execute_result.issues:
            print("Smoke flow failed at execute:")
            for issue in execute_result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        evidence_result = generate_evidence_manifest(root, state.run_id)
        if evidence_result.issues:
            print("Smoke flow failed at evidence:")
            for issue in evidence_result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        print(json.dumps(
            {
                "run_id": state.run_id,
                "status": execute_result.status,
                "snapshot": str(publish_result.path.relative_to(root)),
                "execution_plan": f".loop/runs/{state.run_id}/execution-plan.json",
                "execution_graph": f".loop/runs/{state.run_id}/execution-graph.json",
                "tool_contract_report": f".loop/runs/{state.run_id}/tool-contract-report.json",
                "evidence_manifest": f".loop/runs/{state.run_id}/evidence-manifest.json",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 0

    if args.command == "revise-spec-from-scr":
        result = create_revision_draft_from_scr(
            root,
            (root / args.spec_change_request).resolve(),
            (root / args.base_snapshot).resolve(),
        )
        if result.issues:
            print("Revision draft generation failed:")
            for issue in result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        assert result.path is not None
        print(json.dumps(
            {
                "change_spec": str(result.path.relative_to(root)),
                "spec_id": result.payload["spec_id"],
                "project": result.payload["project"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 0

    if args.command == "approve-revision-spec":
        result = approve_revision_draft(
            root,
            (root / args.change_spec).resolve(),
            approved_by=args.approved_by,
            reason=args.reason,
            status=args.status,
        )
        if result.issues:
            print("Revision approval failed:")
            for issue in result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        assert result.path is not None
        print(json.dumps(
            {
                "change_spec": str(result.path.relative_to(root)),
                "approval": result.payload["approval"],
                "spec_id": result.payload["spec_id"],
                "project": result.payload["project"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 0

    if args.command == "create-run":
        state = GraphRunStore(root).create((root / args.snapshot).resolve(), run_id=args.run_id)
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.command == "run-state":
        store = GraphRunStore(root)
        state = store.read_state(args.run_id)
        result = store.validate(args.run_id)
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        if result.ok:
            print("GraphRun validation passed.")
            return 0
        print("GraphRun validation failed:")
        for issue in result.issues:
            location = f" ({issue.path})" if issue.path else ""
            print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
        return 1

    if args.command == "plan":
        plan = generate_execution_plan(root, args.run_id)
        if plan.issues:
            print("ExecutionPlan generation failed:")
            for issue in plan.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        print(json.dumps(plan.payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.command == "compile-graph":
        graph = compile_execution_graph(root, args.run_id)
        if graph.issues:
            print("ExecutionGraph compilation failed:")
            for issue in graph.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        print(json.dumps(graph.payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.command == "tool-contract":
        result = generate_tool_contract_report(root, args.run_id)
        if result.issues:
            print("ToolContract validation failed:")
            for issue in result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        print(json.dumps(result.payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.command == "evidence":
        result = generate_evidence_manifest(root, args.run_id)
        if result.issues:
            print("Evidence manifest generation failed:")
            for issue in result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        print(json.dumps(result.payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.command == "execute":
        retry_policy = load_retry_policy((root / args.retry_policy).resolve()) if args.retry_policy else None
        executor = DRGExecutor(root, retry_policy=retry_policy)
        result = executor.run(args.run_id, max_steps=args.max_steps)
        # V-11: continue if run() returned RUNNING (retry was scheduled but
        # run() returned instead of continuing the while loop)
        while result.status == "RUNNING":
            result = executor.run(args.run_id, max_steps=args.max_steps)
        if result.issues:
            print("DRG execution failed:")
            for issue in result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        print(json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "executed_nodes": result.executed_nodes,
                "blocked_nodes": result.blocked_nodes,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 0

    if args.command == "reset-node":
        result = RetryManager(root).reset_node(args.run_id, args.node_id)
        if result.issues:
            print("Node reset failed:")
            for issue in result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        print(json.dumps(
            {
                "run_id": result.run_id,
                "reset_node": result.reset_node,
                "invalidated_nodes": result.invalidated_nodes,
                "removed_artifacts": result.removed_artifacts,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 0

    if args.command == "supersede-run":
        result = SupersedeManager(root).supersede(
            args.old_run_id,
            (root / args.new_snapshot).resolve(),
            new_run_id=args.new_run_id,
        )
        if result.issues:
            print("GraphRun supersede failed:")
            for issue in result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        print(json.dumps(
            {
                "old_run_id": result.old_run_id,
                "old_status": result.old_status,
                "new_run_id": result.new_run_id,
                "new_snapshot_uri": result.new_snapshot_uri,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 0

    if args.command == "resume-run":
        result = DRGExecutor(root).resume_spec_change(args.run_id, (root / args.new_snapshot).resolve())
        if result.issues:
            print("Resume from AWAITING_SPEC_CHANGE failed:")
            for issue in result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        print(json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "executed_nodes": result.executed_nodes,
                "blocked_nodes": result.blocked_nodes,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 0

    if args.command == "resume-human-review":
        result = DRGExecutor(root).resume_human_review(args.run_id, args.decision, args.reason)
        if result.issues:
            print("Resume from AWAITING_HUMAN failed:")
            for issue in result.issues:
                location = f" ({issue.path})" if issue.path else ""
                print(f"- [{issue.severity}] {issue.code}: {issue.message}{location}")
            return 1
        print(json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "executed_nodes": result.executed_nodes,
                "blocked_nodes": result.blocked_nodes,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
