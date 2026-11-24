"""Agent-Harness 模型层稳定契约。

该模块位于 Agent Context 与供应商适配器的边界，定义与具体 SDK 无关的消息、请求、
响应、用量和错误，使未来 Agent Loop 只依赖 Harness 自有协议。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelMessage:
    """表示一条模型消息，位于 Harness Context Manager 输出到 Gateway 的流程边界。"""

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        """序列化消息，供 Harness Provider Adapter 组装供应商请求。"""
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ModelUsage:
    """记录模型资源消耗，位于 Harness 预算累计和可观测性流程。"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: float | None = None

    def add(self, other: "ModelUsage") -> "ModelUsage":
        """累加重试用量，确保 Harness 预算不会因模型重试而被重置。"""
        combined_cost = None if self.cost is None or other.cost is None else self.cost + other.cost
        return ModelUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost=combined_cost,
        )

    def to_dict(self) -> dict[str, int | float | None]:
        """序列化用量，供 Harness 事件、Checkpoint 和最终结果记录。"""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost": self.cost,
        }


@dataclass(frozen=True)
class ModelRequest:
    """描述一次供应商无关模型请求，位于 Harness Gateway 的输入边界。"""

    request_id: str
    model: str
    messages: tuple[ModelMessage, ...]
    max_output_tokens: int
    temperature: float | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    """描述一次模型响应，位于 Harness Provider Adapter 返回 Gateway 的边界。"""

    response_id: str
    model: str
    text: str
    finish_reason: str
    usage: ModelUsage = field(default_factory=ModelUsage)
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelCallResult:
    """封装 Gateway 最终结果，位于 Harness 模型调用与 Agent Runtime 的交接环节。"""

    response: ModelResponse
    structured_output: Any
    attempts: int
    total_usage: ModelUsage


class ModelError(Exception):
    """归一化模型错误，位于 Harness Provider/Gateway 向上层分类失败的边界。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        usage: ModelUsage | None = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.usage = usage or ModelUsage()
        self.attempts = attempts


class ModelProvider(Protocol):
    """抽象模型供应商，位于 Harness Gateway 与具体远程 API 的依赖反转边界。"""

    def complete(self, request: ModelRequest, *, timeout_seconds: float) -> ModelResponse:
        """执行一次模型请求，供 Harness Gateway 施加超时、重试和校验策略。"""
        ...
