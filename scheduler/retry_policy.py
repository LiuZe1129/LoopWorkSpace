from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import EventLogger
from .retry import InvalidationResult, RetryManager
from .runs import GraphRunStore, RunState


@dataclass(frozen=True)
class RetryDecision:
    action: str
    node_id: str
    retry_count: int
    reason: str
    invalidation: InvalidationResult | None = None


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    node_max_retries: dict[str, int] = field(default_factory=dict)
    node_type_max_retries: dict[str, int] = field(default_factory=dict)
    reason_max_retries: dict[str, int] = field(default_factory=dict)
    retryable_node_types: set[str] = field(
        default_factory=lambda: {
            "governance",
            "plan_gate",
            "architecture",
            "compiler",
            "implementation",
            "test",
            "gate",
            "review",
            "deploy",
            "conformance",
        }
    )

    def max_retries_for(self, node_id: str, node_type: str, reason: str = "") -> int:
        if node_id in self.node_max_retries:
            return int(self.node_max_retries[node_id])
        for pattern, limit in self.reason_max_retries.items():
            if pattern and pattern in reason:
                return int(limit)
        if node_type in self.node_type_max_retries:
            return int(self.node_type_max_retries[node_type])
        return int(self.max_retries)

    def can_retry(self, state: RunState, node_id: str, node_type: str, reason: str = "") -> bool:
        if node_type not in self.retryable_node_types:
            return False
        return int(state.retry_count.get(node_id, 0) or 0) < self.max_retries_for(node_id, node_type, reason)


def load_retry_policy(path: Path) -> RetryPolicy:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return retry_policy_from_payload(payload)


def retry_policy_from_payload(payload: dict[str, Any]) -> RetryPolicy:
    retryable = payload.get("retryable_node_types")
    return RetryPolicy(
        max_retries=int(payload.get("max_retries", payload.get("default_max_retries", 3))),
        node_max_retries=int_dict(payload.get("node_max_retries", {}) or {}),
        node_type_max_retries=int_dict(payload.get("node_type_max_retries", {}) or {}),
        reason_max_retries=int_dict(payload.get("reason_max_retries", {}) or {}),
        retryable_node_types=set(retryable) if retryable is not None else RetryPolicy().retryable_node_types,
    )


class RetryPolicyRunner:
    def __init__(self, root: Path, policy: RetryPolicy | None = None):
        self.root = root
        self.policy = policy or RetryPolicy()
        self.store = GraphRunStore(root)
        self.events = EventLogger(root)
        self.retry_manager = RetryManager(root)

    def handle_failure(self, state: RunState, node_payload: dict, error: str) -> RetryDecision:
        node_id = str(node_payload["id"])
        node_type = str(node_payload["type"])
        self.events.append_run(state.run_id, "node_failed", {"run_id": state.run_id, "node_id": node_id, "error": error})

        effective_max_retries = self.policy.max_retries_for(node_id, node_type, error)

        if self.policy.can_retry(state, node_id, node_type, error):
            invalidation = self.retry_manager.reset_node(state.run_id, node_id)
            retry_count = self.store.read_state(state.run_id).retry_count.get(node_id, 0)
            self.events.append_run(
                state.run_id,
                "node_retry_scheduled",
                {
                    "run_id": state.run_id,
                    "node_id": node_id,
                    "retry_count": retry_count,
                    "max_retries": effective_max_retries,
                    "reason": error,
                },
            )
            return RetryDecision(
                action="retry",
                node_id=node_id,
                retry_count=retry_count,
                reason=error,
                invalidation=invalidation,
            )

        state.current_nodes = []
        if node_id not in state.failed_nodes:
            state.failed_nodes.append(node_id)
        state.status = "ESCALATED"
        state.last_error = error
        retry_count = int(state.retry_count.get(node_id, 0) or 0)
        self.store.write_state(state, f"{len(state.completed_nodes):03d}-{node_id}-escalated.json")
        self.events.append_run(
            state.run_id,
            "run_escalated",
            {
                "run_id": state.run_id,
                "node_id": node_id,
                "retry_count": retry_count,
                "max_retries": effective_max_retries,
                "reason": error,
            },
        )
        return RetryDecision(action="escalate", node_id=node_id, retry_count=retry_count, reason=error)


def int_dict(payload: dict[str, Any]) -> dict[str, int]:
    return {str(key): int(value) for key, value in payload.items()}
