"""Agent-Harness 模型上下文管理器。

该模块位于 Task/AgentProfile 与 Model Gateway 之间，只把明确允许的 Prompt、任务契约、
历史和 Observation 组装为消息，并按确定策略裁剪可选历史，禁止 Agent 任意读取文件。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .contracts import Task
from .errors import AgentProfileIntegrityError, ContextBudgetError
from .integration import AgentProfile
from .models.base import ModelMessage, ModelRequest


def estimate_tokens(text: str) -> int:
    """保守估算 Token 数，位于 Harness 无供应商 Tokenizer 时的上下文预算预检环节。"""
    if not text:
        return 0
    # UTF-8 字节数可对中英文给出稳定保守估算；真实用量最终以 Provider 返回值为准。
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


@dataclass(frozen=True)
class ContextWindow:
    """描述已裁剪模型上下文，位于 Harness Context Manager 到 Gateway 的交接边界。"""

    messages: tuple[ModelMessage, ...]
    estimated_input_tokens: int
    dropped_messages: int
    context_hash: str


class ContextManager:
    """构建受预算约束的模型消息，位于 Harness 单 Agent 每轮推理之前。"""

    def build(
        self,
        task: Task,
        profile: AgentProfile,
        *,
        history: tuple[ModelMessage, ...] = (),
        observations: tuple[ModelMessage, ...] = (),
        model_tokens_used: int = 0,
    ) -> ContextWindow:
        """组装并裁剪上下文，位于 Harness 调用 Model Gateway 之前的准备环节。"""
        if task.agent_profile != profile.to_ref():
            raise AgentProfileIntegrityError("Task AgentProfile reference does not match loaded profile")
        if model_tokens_used < 0:
            raise ValueError("model_tokens_used must not be negative")
        # 为本轮最大输出预留预算；重试和上一轮用量由 Attempt 累计后传入此处。
        remaining_task_tokens = task.budget.max_model_tokens - model_tokens_used
        input_budget = min(profile.max_input_tokens, max(0, remaining_task_tokens - profile.max_output_tokens))
        task_payload = {
            "task_id": task.task_id,
            "objective": task.objective,
            "input_refs": list(task.input_refs),
            "acceptance": list(task.acceptance),
            "permissions": task.permissions.to_dict(),
            "version_set": dict(task.version_set),
        }
        required = (
            ModelMessage("system", profile.prompt),
            ModelMessage(
                "user",
                "请仅根据以下 Task Contract 输出符合约定 Schema 的结果：\n"
                + json.dumps(task_payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        required_tokens = self._message_tokens(required)
        if required_tokens > input_budget:
            raise ContextBudgetError(
                f"required context exceeds available input budget: {required_tokens} > {input_budget}"
            )
        optional = list(history + observations)
        dropped = 0
        while optional and self._message_tokens(required + tuple(optional)) > input_budget:
            optional.pop(0)
            dropped += 1
        messages = required + tuple(optional)
        estimated = self._message_tokens(messages)
        serialized = json.dumps([item.to_dict() for item in messages], ensure_ascii=False, sort_keys=True)
        return ContextWindow(
            messages=messages,
            estimated_input_tokens=estimated,
            dropped_messages=dropped,
            context_hash=f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}",
        )

    def build_request(
        self,
        window: ContextWindow,
        profile: AgentProfile,
        *,
        request_id: str,
    ) -> ModelRequest:
        """构造带版本闭包的请求，位于 Harness Context 输出到 Model Gateway 的交接环节。"""
        return ModelRequest(
            request_id=request_id,
            model=profile.model,
            messages=window.messages,
            max_output_tokens=profile.max_output_tokens,
            temperature=profile.temperature,
            metadata={
                "agent_id": profile.agent_id,
                "profile_version": profile.version,
                "profile_hash": profile.content_hash,
                "context_hash": window.context_hash,
                "model": profile.model,
            },
        )

    def _message_tokens(self, messages: tuple[ModelMessage, ...]) -> int:
        return sum(estimate_tokens(message.role) + estimate_tokens(message.content) + 2 for message in messages)
