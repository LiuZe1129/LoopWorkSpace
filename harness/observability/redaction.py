"""Agent-Harness 日志脱敏策略。

该模块位于外部请求信息进入 Event/Log 之前，递归清除 API Key、Authorization、Token、
Cookie 等敏感字段，并遮蔽文本中的常见 Bearer 和密钥形态。
"""

from __future__ import annotations

import re
from typing import Any


REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth_token",
    "token",
    "secret",
    "client_secret",
    "password",
    "cookie",
    "set-cookie",
}
TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/=]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def redact_text(value: str) -> str:
    """遮蔽文本密钥，位于 Harness 模型错误和可观测性记录写入前的流程边界。"""
    redacted = value
    for pattern in TEXT_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def redact_value(value: Any) -> Any:
    """递归脱敏结构化值，位于 Harness Event/Log 接收外部元数据的统一入口。"""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            result[str(key)] = REDACTED if _is_sensitive_key(normalized) else redact_value(item)
        return result
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def _is_sensitive_key(normalized: str) -> bool:
    if normalized in SENSITIVE_KEYS:
        return True
    return normalized.endswith(("_api_key", "_secret", "_password", "_access_token", "_refresh_token"))
