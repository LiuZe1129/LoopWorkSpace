"""Agent-Harness Tool Registry。

该模块位于 Tool Router 查找与 Schema 校验环节，只注册工具契约而不暴露执行器，
避免 Agent 绕过 Policy 和 Sandbox 直接持有文件或命令执行能力。
"""

from __future__ import annotations

from pathlib import PurePosixPath

from harness.errors import AlreadyExistsError
from harness.schema_validation import validate_json_schema

from .contracts import ToolDefinition, ToolRisk


class ToolRegistry:
    """保存版本化 ToolDefinition，位于 Harness Router 的能力发现边界。"""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        """注册唯一工具名，位于 Harness 启动时构建允许能力集合的环节。"""
        if definition.name in self._definitions:
            raise AlreadyExistsError(f"Tool already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        """查找工具定义，位于 Harness Router 接收模型 ToolCall 后的第一步。"""
        return self._definitions.get(name)

    def validate_arguments(self, definition: ToolDefinition, arguments: dict) -> list[str]:
        """校验工具参数，位于 Harness Policy 与任何副作用发生之前的流程边界。"""
        errors = validate_json_schema(arguments, definition.input_schema)
        if not errors and definition.name in {"read_file", "write_file"}:
            value = str(arguments.get("path", ""))
            path = PurePosixPath(value)
            if (
                not value
                or "\\" in value
                or "\x00" in value
                or path.is_absolute()
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                errors.append("$.path: must stay within the Sandbox workspace")
        if not errors and definition.name == "shell_exec":
            if any("\x00" in str(item) for item in arguments.get("argv", [])):
                errors.append("$.argv: null characters are not allowed")
        return errors

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """列出工具定义，位于 Harness Agent Context 暴露允许工具 Schema 的环节。"""
        return tuple(self._definitions[name] for name in sorted(self._definitions))


def build_mvp_registry() -> ToolRegistry:
    """构建三个基础工具，位于 Harness MVP 启动时的 Tool 能力装配环节。"""
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read_file",
            version="1.0.0",
            input_schema={
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 512}},
                "additionalProperties": False,
            },
            risk=ToolRisk.LOW,
            timeout_seconds=30,
            side_effecting=False,
            idempotent=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="write_file",
            version="1.0.0",
            input_schema={
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 512},
                    "content": {"type": "string", "maxLength": 10_485_760},
                },
                "additionalProperties": False,
            },
            risk=ToolRisk.MEDIUM,
            timeout_seconds=30,
            side_effecting=True,
            idempotent=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="shell_exec",
            version="1.0.0",
            input_schema={
                "type": "object",
                "required": ["argv"],
                "properties": {
                    "argv": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 128,
                        "items": {"type": "string", "maxLength": 4096},
                    }
                },
                "additionalProperties": False,
            },
            risk=ToolRisk.HIGH,
            timeout_seconds=300,
            side_effecting=True,
            idempotent=False,
        )
    )
    return registry
