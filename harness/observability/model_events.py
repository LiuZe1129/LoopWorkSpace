"""Agent-Harness 模型调用事件适配器。

该模块位于 Model Gateway Recorder 与 EventStore 之间，将每次供应商尝试的版本、耗时、
Token、费用和错误分类写入 Execution 时间线，且不持久化 Prompt、响应正文或凭据。
"""

from __future__ import annotations

from typing import Any

from harness.persistence import EventStore

from .redaction import redact_value


class ModelEventRecorder:
    """持久化模型调用摘要，位于 Harness Gateway 可观测记录到执行事件的桥接边界。"""

    def __init__(self, event_store: EventStore, execution_id: str, attempt_id: str):
        self._event_store = event_store
        self._execution_id = execution_id
        self._attempt_id = attempt_id

    def record(self, payload: dict[str, Any]) -> None:
        """写入脱敏模型事件，位于 Harness 每次模型尝试结束后的审计环节。"""
        self._event_store.append(
            self._execution_id,
            "model.call",
            attempt_id=self._attempt_id,
            payload=redact_value(payload),
        )

    def __call__(self, payload: dict[str, Any]) -> None:
        self.record(payload)
