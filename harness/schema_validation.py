"""Agent-Harness 轻量 JSON Schema 校验器。

该模块位于模型输出与 Tool 参数进入执行流程之前，提供 MVP 所需的 object、array、
基础类型、required、enum 和 additionalProperties 确定性校验，避免不可信输入直接执行。
"""

from __future__ import annotations

from typing import Any


def validate_json_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """校验 MVP JSON Schema 子集，位于 Harness 接受模型或 Tool 输入前的确定性边界。"""
    errors: list[str] = []
    expected_type = schema.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    if expected_type in type_map:
        expected_python_type = type_map[expected_type]
        type_matches = isinstance(value, expected_python_type)
        if expected_type in {"number", "integer"} and isinstance(value, bool):
            type_matches = False
        if not type_matches:
            return [f"{path}: expected {expected_type}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not in enum")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            errors.append(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            errors.append(f"{path}: string is longer than maxLength")
    if isinstance(value, dict):
        properties = schema.get("properties", {}) or {}
        for required in schema.get("required", []) or []:
            if required not in value:
                errors.append(f"{path}.{required}: required property is missing")
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                errors.extend(validate_json_schema(item, child_schema, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key}: additional property is not allowed")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            errors.append(f"{path}: array is shorter than minItems")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            errors.append(f"{path}: array is longer than maxItems")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                errors.extend(validate_json_schema(item, schema["items"], f"{path}[{index}]"))
    return errors
