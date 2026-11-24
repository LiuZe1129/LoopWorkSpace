"""OpenAI Responses API 的 Agent-Harness Provider Adapter。

该模块位于 Model Gateway 与 HTTPS 接口之间，使用标准库调用 Responses API，并把
供应商请求/响应转换为 Harness 契约；API Key 只进入 Authorization Header，不进入事件。
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import ModelError, ModelRequest, ModelResponse, ModelUsage


JsonTransport = Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]]


class OpenAIResponsesAdapter:
    """适配 OpenAI Responses API，位于 Harness 模型层的唯一供应商网络出口。"""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "https://api.openai.com/v1/responses",
        transport: JsonTransport | None = None,
        input_cost_per_million: float | None = None,
        output_cost_per_million: float | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._endpoint = endpoint
        self._transport = transport or self._post_json
        if (input_cost_per_million is None) != (output_cost_per_million is None):
            raise ValueError("input and output pricing must be configured together")
        self._input_cost_per_million = input_cost_per_million
        self._output_cost_per_million = output_cost_per_million
        self._active_responses: dict[str, Any] = {}
        self._cancelled_requests: set[str] = set()
        self._active_lock = threading.Lock()

    def complete(self, request: ModelRequest, *, timeout_seconds: float) -> ModelResponse:
        """调用 Responses API，位于 Harness Gateway 发起单次供应商请求的执行环节。"""
        instructions = "\n\n".join(
            message.content for message in request.messages if message.role == "system"
        )
        input_messages = [
            message.to_dict() for message in request.messages if message.role != "system"
        ]
        payload: dict[str, Any] = {
            "model": request.model,
            "input": input_messages,
            "max_output_tokens": request.max_output_tokens,
        }
        if instructions:
            payload["instructions"] = instructions
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Client-Request-Id": request.request_id,
        }
        with self._active_lock:
            self._cancelled_requests.discard(request.request_id)
        try:
            raw = self._transport(self._endpoint, payload, headers, timeout_seconds)
        except ModelError:
            raise
        except TimeoutError as exc:
            raise ModelError("model_timeout", "model request timed out", retryable=True) from exc
        except HTTPError as exc:
            retryable = exc.code in {408, 409, 429} or exc.code >= 500
            raise ModelError(
                "provider_http_error",
                f"model provider returned HTTP {exc.code}",
                retryable=retryable,
            ) from exc
        except URLError as exc:
            raise ModelError("provider_unavailable", "model provider is unavailable", retryable=True) from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ModelError("provider_protocol_error", "invalid model provider response", retryable=False) from exc
        try:
            return self._decode_response(raw, request.model)
        except ModelError:
            raise
        except (TypeError, ValueError) as exc:
            raise ModelError("provider_protocol_error", "invalid model provider response", retryable=False) from exc

    def cancel(self, request_id: str) -> None:
        """关闭活动 HTTP 响应，位于 Harness 用户取消向模型供应商传播的执行环节。"""
        with self._active_lock:
            self._cancelled_requests.add(request_id)
            response = self._active_responses.get(request_id)
        if response is not None:
            response.close()

    def _post_json(
        self,
        endpoint: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = Request(endpoint, data=encoded, headers=headers, method="POST")
        request_id = headers["X-Client-Request-Id"]
        with self._active_lock:
            if request_id in self._cancelled_requests:
                raise TimeoutError("model request cancelled")
        with urlopen(http_request, timeout=timeout_seconds) as response:
            with self._active_lock:
                self._active_responses[request_id] = response
            try:
                return json.loads(response.read().decode("utf-8"))
            finally:
                with self._active_lock:
                    self._active_responses.pop(request_id, None)

    def _decode_response(self, payload: dict[str, Any], requested_model: str) -> ModelResponse:
        text = payload.get("output_text")
        if not isinstance(text, str):
            text = self._extract_output_text(payload)
        if not text:
            raise ModelError("empty_model_output", "model response contains no output text", retryable=True)
        usage_payload = payload.get("usage", {}) or {}
        input_tokens = int(usage_payload.get("input_tokens", 0) or 0)
        output_tokens = int(usage_payload.get("output_tokens", 0) or 0)
        cost = None
        if self._input_cost_per_million is not None and self._output_cost_per_million is not None:
            cost = (
                input_tokens * self._input_cost_per_million
                + output_tokens * self._output_cost_per_million
            ) / 1_000_000
        return ModelResponse(
            response_id=str(payload.get("id", "")),
            model=str(payload.get("model", requested_model)),
            text=text,
            finish_reason=str(payload.get("status", "completed")),
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=int(usage_payload.get("total_tokens", input_tokens + output_tokens) or 0),
                cost=cost,
            ),
            provider_metadata={"provider": "openai", "status": payload.get("status")},
        )

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
        fragments: list[str] = []
        for item in payload.get("output", []) or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []) or []:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    fragments.append(str(content.get("text", "")))
        return "\n".join(fragment for fragment in fragments if fragment)
