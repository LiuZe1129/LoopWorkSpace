from __future__ import annotations

from dataclasses import dataclass, field

from .artifacts import ArtifactManager
from .schema import validate_done_transition
from .state import DYNAMIC_PRODUCT_MAP, LEGAL_TRANSITIONS, TaskCard, ValidationIssue


@dataclass
class TransitionCheck:
    allowed: bool
    current: str
    target: str
    verified_artifacts: list[str] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


class TransitionValidator:
    def __init__(self, artifacts: ArtifactManager):
        self.artifacts = artifacts

    def validate(self, task: TaskCard, target: str) -> TransitionCheck:
        current = task.status
        check = TransitionCheck(allowed=True, current=current, target=target)

        if target not in LEGAL_TRANSITIONS.get(current, set()):
            check.allowed = False
            check.issues.append(
                ValidationIssue(
                    code="transition.illegal",
                    severity="critical",
                    message=f"illegal transition: {current} -> {target}",
                    path=str(task.path),
                )
            )
            return check

        required = self.required_artifacts(task, target)
        check.verified_artifacts = required
        for name in required:
            if not self.artifacts.exists_nonempty(task, name):
                check.allowed = False
                check.issues.append(
                    ValidationIssue(
                        code="transition.missing_artifact",
                        severity="critical",
                        message=f"{current} -> {target} requires artifact: {name}",
                        path=str(self.artifacts.artifact_path(task, name)),
                    )
                )

        if target == "ENV_CHECK":
            architecture = self.artifacts.read_json(task, "architecture.json")
            env_requirements = None
            if architecture:
                env_requirements = architecture.get("architecture", {}).get("environment_requirements")
            if not env_requirements:
                check.allowed = False
                check.issues.append(
                    ValidationIssue(
                        code="transition.missing_env_requirements",
                        severity="critical",
                        message="APPROVED -> ENV_CHECK requires architecture.environment_requirements",
                        path=str(self.artifacts.artifact_path(task, "architecture.json")),
                    )
                )

        if target == "DONE":
            done_result = validate_done_transition(task, self.artifacts)
            if not done_result.ok:
                check.allowed = False
                check.issues.extend(done_result.issues)

        if target == "RUNNING" and task.assigned_to in {"frontend-dev", "client-dev"}:
            if not self.artifacts.ui_review_approved(task):
                check.allowed = False
                check.issues.append(
                    ValidationIssue(
                        code="transition.ui_review_not_approved",
                        severity="critical",
                        message="frontend/client implementation requires ui-review.json verdict=APPROVE",
                        path=str(self.artifacts.artifact_path(task, "ui-review.json")),
                    )
                )

        return check

    def required_artifacts(self, task: TaskCard, target: str) -> list[str]:
        current = task.status
        if current == "RUNNING" and target == "AWAITING_AUTO_GATE":
            if task.assigned_to is None:
                return []
            return DYNAMIC_PRODUCT_MAP.get(task.assigned_to, [])
        if target == "AWAITING_REVIEW":
            return ["gate-result.json"]
        if target == "APPROVED":
            return ["review.json"]
        if target == "ENV_CHECK":
            return ["architecture.json"]
        if current == "ENV_CHECK" and target == "AWAITING_HUMAN":
            return ["env-check-result.json"]
        if target == "RUNNING" and task.assigned_to in {"frontend-dev", "client-dev"}:
            return self.artifacts.required_before_implementation(task)
        if target == "DONE":
            return self.artifacts.required_for_done(task)
        return []
