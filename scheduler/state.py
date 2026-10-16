from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"DONE", "FAILED", "ESCALATED", "SUPERSEDED"}

# ⚠️ 遗留 Mode B (TaskCard) 状态机 — 不用于 Mode A (GraphFlow RunState)。
# Mode A 使用 runs.py:RUN_LEGAL_TRANSITIONS。
# 本表仅供 TransitionValidator (check-transition CLI) 和 schema.py:validate_task_card
# 校验遗留 TaskCard 状态转换。GraphFlow Run 不使用这些状态。
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"DISPATCHED", "AWAITING_HUMAN"},
    "DISPATCHED": {"RUNNING", "FAILED", "ESCALATED", "AWAITING_HUMAN", "AWAITING_SPEC_CHANGE"},
    "RUNNING": {"AWAITING_AUTO_GATE", "FAILED", "ESCALATED", "AWAITING_HUMAN", "AWAITING_SPEC_CHANGE"},
    "AWAITING_AUTO_GATE": {"AWAITING_REVIEW", "RETRY", "FAILED", "ESCALATED", "AWAITING_HUMAN", "AWAITING_SPEC_CHANGE"},
    "AWAITING_REVIEW": {"APPROVED", "REJECTED", "FAILED", "ESCALATED", "AWAITING_HUMAN", "AWAITING_SPEC_CHANGE"},
    "APPROVED": {"ENV_CHECK", "DONE", "PENDING", "AWAITING_HUMAN"},
    "ENV_CHECK": {"AWAITING_HUMAN"},
    "REJECTED": {"RETRY", "ESCALATED"},
    "RETRY": {"DISPATCHED"},
    "AWAITING_HUMAN": {
        "DISPATCHED",
        "RUNNING",
        "AWAITING_AUTO_GATE",
        "AWAITING_REVIEW",
        "APPROVED",
        "ENV_CHECK",
        "ESCALATED",
        "AWAITING_SPEC_CHANGE",
    },
    "AWAITING_SPEC_CHANGE": {"SUPERSEDED", "ESCALATED", "AWAITING_HUMAN"},
    "DONE": set(),
    "FAILED": set(),
    "ESCALATED": set(),
    "SUPERSEDED": set(),
}

DYNAMIC_PRODUCT_MAP: dict[str, list[str]] = {
    "spec-agent": ["spec.json"],
    "orchestrator-llm": ["assignment.json"],
    "architect-agent": ["architecture.json"],
    "ui-design-agent": ["ui-design.json"],
    "backend-dev": ["backend.diff"],
    "frontend-dev": ["frontend.diff"],
    "client-dev": ["client.diff"],
    "test-agent": ["test-report.json"],
    "review-agent": ["review.json"],
    "devops-agent": ["deploy-result.json"],
}

ARCHITECTURE_REQUIRED_AGENTS = [
    "spec-agent",
    "orchestrator-llm",
    "architect-agent",
    "gate-auto",
    "review-agent",
]

DEVELOPMENT_REQUIRED_BASE = [
    "ui-design-agent",
    "backend-dev",
    "frontend-dev",
    "test-agent",
    "gate-auto",
    "review-agent",
    "devops-agent",
]


@dataclass
class DoneItem:
    id: str
    check: str
    expect: str
    status: str = "pending"
    gate_stage: str = "final"


@dataclass
class TaskCard:
    path: Path
    raw_frontmatter: dict[str, Any]
    title: str
    done_items: list[DoneItem]
    body: str

    @property
    def id(self) -> str:
        return str(self.raw_frontmatter.get("id", ""))

    @property
    def project(self) -> str:
        return str(self.raw_frontmatter.get("project", ""))

    @property
    def status(self) -> str:
        return str(self.raw_frontmatter.get("status", "PENDING"))

    @property
    def phase(self) -> str:
        return str(self.raw_frontmatter.get("phase", "architecture"))

    @property
    def project_type(self) -> str:
        return str(self.raw_frontmatter.get("project_type", "web"))

    @property
    def round(self) -> int:
        return int(self.raw_frontmatter.get("round", 0) or 0)

    @property
    def arch_round(self) -> int:
        return int(self.raw_frontmatter.get("arch_round", 1) or 1)

    @property
    def assigned_to(self) -> str | None:
        value = self.raw_frontmatter.get("assigned_to")
        return None if value in (None, "null", "") else str(value)

    @property
    def graph_node(self) -> str | None:
        value = self.raw_frontmatter.get("graph_node")
        return None if value in (None, "null", "") else str(value)

    @property
    def pre_awaiting_human_node(self) -> str | None:
        value = self.raw_frontmatter.get("pre_awaiting_human_node")
        return None if value in (None, "null", "") else str(value)

    @property
    def executed_agents(self) -> list[str]:
        return list(self.raw_frontmatter.get("executed_agents", []) or [])

    @property
    def executed_agents_arch(self) -> list[str]:
        return list(self.raw_frontmatter.get("executed_agents_arch", []) or [])

    @property
    def executed_agents_subtask(self) -> list[str]:
        return list(self.raw_frontmatter.get("executed_agents_subtask", []) or [])


@dataclass
class RuntimeState:
    task_card: TaskCard
    current_node: str = "start"
    artifact_refs: list[dict[str, str]] = field(default_factory=list)
    retry_count: int = 0
    run_id: str | None = None


@dataclass
class ValidationIssue:
    code: str
    severity: str
    message: str
    path: str | None = None


@dataclass
class ValidationResult:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, code: str, severity: str, message: str, path: str | None = None) -> None:
        self.issues.append(ValidationIssue(code, severity, message, path))
        if severity in {"error", "critical"}:
            self.ok = False
