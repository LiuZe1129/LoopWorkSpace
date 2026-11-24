"""Agent-Harness 模型访问基础设施。

该包位于 Context Manager 与外部模型供应商之间，统一请求契约、Provider Adapter、
Gateway 重试、结构化输出校验和用量统计，避免 Agent Runtime 直接依赖供应商 SDK。
"""

from .base import (
    ModelCallResult,
    ModelError,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from harness.schema_validation import validate_json_schema

from .gateway import ModelGateway
from .provider_adapter import OpenAIResponsesAdapter

__all__ = [
    "ModelCallResult",
    "ModelError",
    "ModelGateway",
    "ModelMessage",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelUsage",
    "OpenAIResponsesAdapter",
    "validate_json_schema",
]
