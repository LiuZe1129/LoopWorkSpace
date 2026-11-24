"""Agent-Harness MVP 运维命令行入口。

该模块位于操作者与 Harness Controller/TaskRunner 之间，提供 Task 登记、真实运行、
状态、事件、结果、取消和恢复标记；正式运行固定使用 OpenAI Provider 与 Docker Sandbox。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from .agent_loop import AgentLoop
from .contracts import Task
from .controller import HarnessController
from .evaluation import EvaluationReport, VersionComparator
from .integration import AgentProfileStore
from .models import ModelGateway, OpenAIResponsesAdapter
from .observability import MetricsCollector, TimelineBuilder
from .persistence import ResultStore
from .sandbox import DockerSandboxRuntime, SandboxManager
from .task_runner import HarnessTaskRunner
from .tools import PolicyEngine, ToolRouter, build_mvp_registry
from .tools.ledger import ToolLedger
from .validation import DeterministicValidator
from .runtime_factory import RuntimeFactory


def build_parser() -> argparse.ArgumentParser:
    """构建 Harness CLI 参数树，位于开发运维请求进入执行基础设施的入口。"""
    parser = argparse.ArgumentParser(description="Agent-Harness MVP operations")
    parser.add_argument("--root", default=".", help="repository root")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("task-create", help="register an immutable Task JSON")
    create.add_argument("task_json")
    run = commands.add_parser("task-run", help="run a registered Task with Docker Sandbox")
    run.add_argument("task_id")
    state = commands.add_parser("task-state", help="print TaskExecution state")
    state.add_argument("execution_id")
    events = commands.add_parser("task-events", help="print Harness execution events")
    events.add_argument("execution_id")
    result = commands.add_parser("task-result", help="print final TaskResult")
    result.add_argument("execution_id")
    timeline = commands.add_parser("task-timeline", help="print redacted execution timeline")
    timeline.add_argument("execution_id")
    metrics = commands.add_parser("task-metrics", help="print execution metrics")
    metrics.add_argument("execution_id")
    cancel = commands.add_parser("task-cancel", help="persist cancellation request")
    cancel.add_argument("execution_id")
    cancel.add_argument("--reason", default="operator_requested")
    resume = commands.add_parser("task-resume", help="mark a paused execution runnable")
    resume.add_argument("execution_id")
    compare = commands.add_parser("evaluation-compare", help="compare two evaluation reports")
    compare.add_argument("baseline")
    compare.add_argument("candidate")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行 Harness 运维命令，位于 CLI 输入到 Controller 或 TaskRunner 的分派环节。"""
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    controller = HarnessController(root)
    if args.command == "task-create":
        task = Task.from_dict(json.loads(Path(args.task_json).read_text(encoding="utf-8")))
        controller.register_task(task)
        print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "task-run":
        runtime = RuntimeFactory(root).build()
        task = runtime.controller.task_store.load_task(args.task_id)
        profile = AgentProfileStore(root).load(task.agent_profile)
        print(json.dumps(runtime.runner.run(task, profile).to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "task-state":
        print(json.dumps(controller.task_store.load_execution(args.execution_id).to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "task-events":
        payload = [event.to_dict() for event in controller.event_store.read(args.execution_id)]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "task-result":
        print(json.dumps(ResultStore(root).load_result(args.execution_id).to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "task-timeline":
        print(json.dumps(TimelineBuilder(root).build(args.execution_id).to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "task-metrics":
        print(json.dumps(MetricsCollector(root).collect_execution(args.execution_id).to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "task-cancel":
        execution = controller.request_cancel(args.execution_id, reason=args.reason)
        print(json.dumps(execution.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "task-resume":
        runtime = RuntimeFactory(root).build()
        execution = runtime.controller.task_store.load_execution(args.execution_id)
        task = runtime.controller.task_store.load_task(execution.task_id)
        profile = AgentProfileStore(root).load(task.agent_profile)
        result = runtime.runner.resume(args.execution_id, profile)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "evaluation-compare":
        baseline = EvaluationReport.from_dict(json.loads(Path(args.baseline).read_text(encoding="utf-8")))
        candidate = EvaluationReport.from_dict(json.loads(Path(args.candidate).read_text(encoding="utf-8")))
        comparison = VersionComparator().compare(baseline, candidate)
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
