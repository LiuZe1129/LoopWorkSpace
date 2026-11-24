from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import shlex
from typing import Any

from .gates import GateCheckResult, run_check
from .runs import GraphRunStore, RunState, read_json
from .specs import load_spec_snapshot
from .state import DoneItem


def run_gate_checks(
    root: Path,
    state: RunState,
    node_id: str,
    acceptance_ids: list[str] | None = None,
    *,
    allow_host_shell: bool = False,
) -> list[GateCheckResult]:
    """选择并执行节点验收规则，位于 GraphFlow Gate 与 Harness Validator 的衔接环节。

    计划/架构检查仍是安全的确定性检查；涉及 Shell 的验收默认被阻断，后续阶段
    将由 Validator 生成 ToolCall，并交给 Policy 与 Sandbox 执行。
    """
    # 计划闸门只验证 ExecutionPlan 结构，不触发任何外部命令。
    # plan_gate_node: verify execution-plan.json exists and has work_packages
    if node_id == "plan_gate_node":
        return _check_plan_gate(root, state)

    # 架构闸门只验证上游 Artifact，属于 GraphFlow 控制平面的确定性检查。
    # architecture_gate_node: verify architecture_impact_node artifact exists
    if node_id == "architecture_gate_node":
        return _check_architecture_gate(root, state)

    # final/test 节点只负责选择验收规则；正式 Harness 模式不会在宿主执行 Shell。
    # final_gate_node and test nodes: run acceptance shell checks.
    # final_gate_node runs ALL acceptance checks (acceptance_ids=None → all);
    # test nodes require explicit acceptance_ids.
    if node_id != "final_gate_node" and not acceptance_ids:
        return []

    snapshot = load_spec_snapshot(root / state.bound_spec.snapshot_uri)
    checks = acceptance_done_items(snapshot.payload.get("acceptance_criteria", []) or [], acceptance_ids=acceptance_ids)
    return [run_check(root, item, allow_host_shell=allow_host_shell) for item in checks]


def _check_plan_gate(root: Path, state: RunState) -> list[GateCheckResult]:
    """plan_gate check: execution-plan.json must exist and contain work_packages."""
    plan_path = GraphRunStore(root).run_dir(state.run_id) / "execution-plan.json"
    if not plan_path.is_file():
        return [GateCheckResult(
            id="execution-plan.json",
            check="file exists",
            expect="found",
            actual="not found",
            exit_code=1,
            passed=False,
        )]
    plan = read_json(plan_path)
    work_packages = plan.get("work_packages", []) or []
    wp_count = len(work_packages)
    return [GateCheckResult(
        id="execution-plan.json",
        check="file exists with work_packages",
        expect="work_packages count > 0",
        actual=f"work_packages count = {wp_count}",
        exit_code=0,
        passed=wp_count > 0,
    )]


def _check_architecture_gate(root: Path, state: RunState) -> list[GateCheckResult]:
    """architecture_gate check: architecture_impact_node artifact must exist."""
    store = GraphRunStore(root)
    arch_path = store.run_dir(state.run_id) / "artifacts" / "architecture_impact_node.json"
    if not arch_path.is_file():
        return [GateCheckResult(
            id="architecture_impact_node.json",
            check="artifact exists",
            expect="found",
            actual="not found",
            exit_code=1,
            passed=False,
        )]
    artifact = read_json(arch_path)
    has_architecture = "architecture" in str(artifact.get("node_id", "")) or artifact.get("status") == "success"
    return [GateCheckResult(
        id="architecture_impact_node.json",
        check="artifact exists and status=success",
        expect="found with status=success",
        actual=f"found with status={artifact.get('status', 'unknown')}",
        exit_code=0,
        passed=has_architecture,
    )]


def acceptance_done_items(items: list[Any], acceptance_ids: list[str] | None = None) -> list[DoneItem]:
    done_items: list[DoneItem] = []
    selected_ids = set(acceptance_ids or [])
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or f"acc-{index}")
        if selected_ids and item_id not in selected_ids:
            continue
        check = item.get("check")
        expect = item.get("expect")
        if not check or not expect:
            continue
        done_items.append(
            DoneItem(
                id=item_id,
                check=str(check),
                expect=str(expect),
                status="pending",
                gate_stage=str(item.get("gate_stage") or "final"),
            )
        )
    return done_items


def build_harness_acceptance(
    root: Path,
    state: RunState,
    node_id: str,
    acceptance_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """把 Spec 验收规则转换为 Harness Validator 输入，位于 GraphFlow Gate 到 Sandbox ToolCall 的边界。"""
    if node_id != "final_gate_node" and not acceptance_ids:
        return []
    snapshot = load_spec_snapshot(root / state.bound_spec.snapshot_uri)
    items = acceptance_done_items(
        snapshot.payload.get("acceptance_criteria", []) or [],
        acceptance_ids=acceptance_ids,
    )
    converted: list[dict[str, Any]] = []
    for item in items:
        try:
            argv = shlex.split(item.check, posix=True)
        except ValueError:
            argv = []
        # Harness 基础工具只接受 argv；含 Shell 运算符的旧规则必须显式失败，禁止回退宿主 Shell。
        if not argv or any(token in {"&&", "||", ";", "|", ">", ">>"} for token in argv):
            converted.append({"id": item.id, "type": "unsupported_shell", "check": item.check})
            continue
        acceptance: dict[str, Any] = {"id": item.id, "type": "command", "argv": argv}
        if item.expect.startswith("exitcode:"):
            value = item.expect.split(":", 1)[1]
            if value == "nonzero":
                acceptance["expected_exit_nonzero"] = True
            else:
                acceptance["expected_exit_code"] = int(value)
        elif item.expect.startswith("contains:"):
            acceptance["stdout_contains"] = item.expect.split(":", 1)[1]
        elif item.expect.startswith("regex:"):
            acceptance["stdout_regex"] = item.expect.split(":", 1)[1]
        elif item.expect.startswith(">="):
            acceptance["stdout_min"] = float(item.expect[2:].strip())
        else:
            acceptance["stdout_equals"] = item.expect
        converted.append(acceptance)
    return converted


def serialize_check_results(results: list[GateCheckResult]) -> list[dict[str, Any]]:
    return [asdict(result) for result in results]
