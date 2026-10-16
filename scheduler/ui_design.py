from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProductShape:
    product_type: str
    target_users: list[str]
    core_workflows: list[str]
    information_architecture: list[str]
    interaction_model: list[str]
    visual_direction: str
    out_of_scope: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        return not self.open_questions


def required_product_shape_fields() -> list[str]:
    return [
        "product_type",
        "target_users",
        "core_workflows",
        "information_architecture",
        "interaction_model",
        "visual_direction",
    ]


def validate_product_shape(payload: dict[str, Any]) -> list[str]:
    missing = [field for field in required_product_shape_fields() if not payload.get(field)]
    questions = payload.get("open_questions", []) or []
    if questions:
        missing.append("open_questions must be resolved before UI design approval")
    return missing
