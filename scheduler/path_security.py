"""路径安全基础设施。

该模块位于 GraphFlow/Harness 的文件系统入口边界，负责在 Run、Event、Artifact
进入持久化流程之前阻止目录穿越、保留设备名和授权根目录之外的访问。
"""

from __future__ import annotations

from pathlib import Path


# Windows 文件系统保留字符和设备名；在跨平台 Harness 中统一按更严格规则处理，
# 避免同一 Task 在 Windows 与 Linux Worker 上产生不同的路径语义。
WINDOWS_RESERVED_CHARS = set('<>:"/\\|?*')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def validate_path_segment(value: str, field_name: str = "path segment") -> str:
    """校验单个路径片段，位于外部标识进入 Harness 文件系统流程的第一道边界。"""
    selected = str(value)
    if not selected or selected in {".", ".."}:
        raise ValueError(f"invalid {field_name}: {selected!r}")
    if len(selected) > 160:
        raise ValueError(f"invalid {field_name}: value is too long")
    if any(ord(char) < 32 for char in selected):
        raise ValueError(f"invalid {field_name}: control characters are not allowed")
    if any(char in WINDOWS_RESERVED_CHARS for char in selected):
        raise ValueError(f"invalid {field_name}: reserved path character in {selected!r}")
    if selected.endswith((" ", ".")):
        raise ValueError(f"invalid {field_name}: trailing spaces or dots are not allowed")
    if selected.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"invalid {field_name}: reserved device name in {selected!r}")
    return selected


def resolve_within(base: Path, *parts: str | Path) -> Path:
    """解析路径并限制在授权根目录内，供 Harness 持久化和 Artifact 流程统一调用。"""
    resolved_base = base.resolve()
    candidate = resolved_base.joinpath(*parts).resolve()
    try:
        candidate.relative_to(resolved_base)
    except ValueError as exc:
        raise ValueError(f"path escapes authorized root: {candidate}") from exc
    return candidate


def safe_child(base: Path, value: str, field_name: str = "path segment") -> Path:
    """组合安全子路径，位于 Run/Event/Artifact 根据业务 ID 定位文件的执行环节。"""
    return resolve_within(base, validate_path_segment(value, field_name))
