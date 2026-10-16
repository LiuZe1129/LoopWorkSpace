from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .state import TaskCard


GLOBAL_ARTIFACTS = {"spec.json", "assignment.json"}
ARCH_ARTIFACTS = {"architecture.json", "env-check-result.json"}
UI_ARTIFACTS = {"product-shape.json", "ui-design.json", "ui-review.json"}


class ArtifactManager:
    def __init__(self, root: Path):
        self.root = root

    def artifact_path(self, task: TaskCard, name: str, round_number: int | None = None) -> Path:
        if name in GLOBAL_ARTIFACTS:
            selected_round = 0
        elif name in ARCH_ARTIFACTS:
            selected_round = task.arch_round
        else:
            selected_round = task.round if round_number is None else round_number
        return self.root / ".loop" / "artifacts" / task.project / task.id / f"round-{selected_round}" / name

    def exists_nonempty(self, task: TaskCard, name: str, round_number: int | None = None) -> bool:
        path = self.artifact_path(task, name, round_number)
        return path.is_file() and path.stat().st_size > 0

    def read_json(self, task: TaskCard, name: str, round_number: int | None = None) -> dict[str, Any] | None:
        path = self.artifact_path(task, name, round_number)
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def required_for_done(self, task: TaskCard) -> list[str]:
        required = ["spec.json", "assignment.json", "architecture.json", "env-check-result.json"]
        if task.phase == "development":
            required.extend(
                [
                    "product-shape.json",
                    "ui-design.json",
                    "ui-review.json",
                    "backend.diff",
                    "client.diff" if task.project_type == "qt" else "frontend.diff",
                    "test-report.json",
                    "gate-result.json",
                    "review.json",
                    "deploy-result.json",
                ]
            )
        else:
            required.extend(["gate-result.json", "review.json"])
        return required

    def required_before_implementation(self, task: TaskCard) -> list[str]:
        """Artifacts required before frontend/client implementation can start."""
        return ["product-shape.json", "ui-design.json", "ui-review.json"]

    def ui_review_approved(self, task: TaskCard) -> bool:
        payload = self.read_json(task, "ui-review.json")
        if not payload:
            return False
        return str(payload.get("verdict", "")) in {"APPROVE", "APPROVED"}

    def missing_for_done(self, task: TaskCard) -> list[str]:
        return [name for name in self.required_for_done(task) if not self.exists_nonempty(task, name)]
