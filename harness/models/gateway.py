"""Agent-Harness Model Gateway。

该模块位于 Context Manager 与 Provider Adapter 之间，统一执行超时参数、有限重试、
JSON 结构校验、跨重试用量累计和脱敏调用记录，是 Agent 访问模型的唯一入口。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from harness.cancellation import CancellationToken
from harness.observability import redact_value
from harness.schema_validation import validate_json_schema

from .base import ModelCallResult, ModelError, ModelProvider, ModelRequest, ModelUsage


CallRecorder = Callable[[dict[str, Any]], None]
Sleeper = Callable[[float], None]


class ModelGateway:
    """执行受控模型调用，位于 Harness Agent Runtime 与供应商网络边界。"""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        max_attempts: int = 3,
        timeout_seconds: float = 120.0,
        recorder: CallRecorder | None = None,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._provider = provider
        self._max_attempts = max_attempts
        self._timeout_seconds = timeout_seconds
        self._recorder = recorder
        self._sleeper = sleeper

    def invoke(
        self,
        request: ModelRequest,
        *,
        output_schema: dict[str, Any] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ModelCallResult:
        """调用模型并校验结果，位于 Harness 单 Agent 每轮推理的模型执行环节。"""
        # 聚合器以已知零费用开始；任一供应商未报告费用时，累计费用转为未知 None。
        total_usage = ModelUsage(cost=0.0)
        last_error: ModelError | None = None
        for attempt_number in range(1, self._max_attempts + 1):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            started = time.monotonic()
            attempt_usage = ModelUsage()
            watcher_stop = threading.Event()
            watcher = None
            if cancellation is not None and callable(getattr(self._provider, "cancel", None)):
                def watch_cancellation() -> None:
                    while not watcher_stop.is_set():
                        if cancellation.wait(0.05):
                            self._provider.cancel(request.request_id)
                            return

                watcher = threading.Thread(target=watch_cancellation, daemon=True)
                watcher.start()
            try:
                response = self._provider.complete(request, timeout_seconds=self._timeout_seconds)
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                attempt_usage = response.usage
                total_usage = total_usage.add(response.usage)
                structured_output: Any = response.text
                if output_schema is not None:
                    try:
                        structured_output = json.loads(response.text)
                    except json.JSONDecodeError as exc:
                        raise ModelError(
                            "invalid_structured_output",
                            "model output is not valid JSON",
                            retryable=True,
                        ) from exc
                    schema_errors = validate_json_schema(structured_output, output_schema)
                    if schema_errors:
                        raise ModelError(
                            "invalid_structured_output",
                            "; ".join(schema_errors),
                            retryable=True,
                        )
                self._record(
                    {
                        "request_id": request.request_id,
                        "response_id": response.response_id,
                        "model": response.model,
                        "attempt": attempt_number,
                        "status": "succeeded",
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "usage": response.usage.to_dict(),
                        "request_metadata": dict(request.metadata),
                    }
                )
                return ModelCallResult(response, structured_output, attempt_number, total_usage)
            except ModelError as exc:
                total_usage = total_usage.add(exc.usage)
                last_error = exc
                recorded_usage = attempt_usage.add(exc.usage)
                self._record(
                    {
                        "request_id": request.request_id,
                        "model": request.model,
                        "attempt": attempt_number,
                        "status": "failed",
                        "error_code": exc.code,
                        "retryable": exc.retryable,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "usage": recorded_usage.to_dict(),
                        "request_metadata": dict(request.metadata),
                    }
                )
                if not exc.retryable or attempt_number >= self._max_attempts:
                    raise ModelError(
                        exc.code,
                        str(exc),
                        retryable=exc.retryable,
                        usage=total_usage,
                        attempts=attempt_number,
                    ) from exc
                self._sleeper(min(0.1 * (2 ** (attempt_number - 1)), 1.0))
            finally:
                watcher_stop.set()
                if watcher is not None:
                    watcher.join(timeout=0.2)
        assert last_error is not None
        raise last_error

    def _record(self, record: dict[str, Any]) -> None:
        if self._recorder is None:
            return
        # Recorder 故障不能改变模型调用结果；记录内容在离开 Gateway 前统一脱敏。
        try:
            self._recorder(redact_value(record))
        except Exception:
            return
