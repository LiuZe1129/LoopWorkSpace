"""检查 Agent-Harness 基础设施是否具备中文职责与流程位置说明。

该脚本位于 CI 发布门禁环节：阶段 0 检查现有 GraphFlow/Harness 桥接点，后续自动
扫描 ``harness/`` 包，防止新基础设施只有实现而没有可供评审和接手的中文设计意图。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CHINESE_PATTERN = re.compile(r"[\u3400-\u9fff]")
ROLE_MARKERS = ("Harness", "GraphFlow", "流程", "边界", "阶段", "框架", "执行")

# 阶段 0 修改过的关键符号。它们处于 GraphFlow 到 Harness 的迁移边界，必须写明
# 当前职责和流程位置；后续新建的 harness/ 包则由全量扫描规则覆盖。
CURRENT_REQUIRED_SYMBOLS: dict[str, set[str]] = {
    "scheduler/path_security.py": {
        "<module>",
        "validate_path_segment",
        "resolve_within",
        "safe_child",
    },
    "scheduler/gates.py": {"run_check"},
    "scheduler/gate_auto.py": {"run_gate_checks"},
    "scheduler/executor.py": {"DRGExecutor", "DRGExecutor.missing_handler_result"},
    "scheduler/runs.py": {"GraphRunStore", "GraphRunStore.run_dir", "relative_to_root"},
    "scheduler/events.py": {"EventLogger", "EventLogger.append_run", "EventLogger.run_events_path"},
    "scheduler/artifacts.py": {"ArtifactManager", "ArtifactManager.artifact_path"},
    "scheduler/tool_contracts.py": {"validate_node_result_artifacts"},
}


def contains_chinese(text: str | None) -> bool:
    """判断说明是否包含中文，位于 Harness 注释门禁的基础语法检查环节。"""
    return bool(text and CHINESE_PATTERN.search(text))


def explains_framework_position(text: str | None) -> bool:
    """判断说明是否提到框架职责或流程位置，避免 Harness 注释只重复函数名称。"""
    return bool(text and any(marker in text for marker in ROLE_MARKERS))


def collect_documented_nodes(tree: ast.AST) -> dict[str, ast.AST]:
    """收集可检查符号，位于 Harness 中文注释门禁解析 Python AST 的执行环节。"""
    nodes: dict[str, ast.AST] = {"<module>": tree}

    def visit(items: list[ast.stmt], prefix: str = "") -> None:
        for item in items:
            if not isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            qualified_name = f"{prefix}.{item.name}" if prefix else item.name
            nodes[qualified_name] = item
            if isinstance(item, ast.ClassDef):
                visit(item.body, qualified_name)

    visit(getattr(tree, "body", []))
    return nodes


def validate_symbol(path: Path, symbol: str, nodes: dict[str, ast.AST]) -> list[str]:
    """验证单个符号的中文职责说明，位于 CI 对 Harness 公共契约逐项判定的环节。"""
    node = nodes.get(symbol)
    relative_path = path.relative_to(REPOSITORY_ROOT).as_posix()
    if node is None:
        return [f"{relative_path}: 缺少要求检查的符号 {symbol}"]
    docstring = ast.get_docstring(node, clean=True)
    errors: list[str] = []
    if not contains_chinese(docstring):
        errors.append(f"{relative_path}:{symbol} 缺少中文 docstring")
    if not explains_framework_position(docstring):
        errors.append(f"{relative_path}:{symbol} 未说明 Harness/GraphFlow 职责或流程位置")
    return errors


def public_harness_symbols(nodes: dict[str, ast.AST]) -> set[str]:
    """选择 Harness 公共符号，位于未来新增基础设施的全量注释检查环节。"""
    required = {"<module>"}
    for qualified_name in nodes:
        if qualified_name == "<module>":
            continue
        leaf_name = qualified_name.rsplit(".", 1)[-1]
        if not leaf_name.startswith("_"):
            required.add(qualified_name)
    return required


def check_python_file(path: Path, required_symbols: set[str]) -> list[str]:
    """检查一个 Python 文件，位于 Harness CI 注释门禁的文件级执行环节。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        return [f"{path}: 无法解析：{exc}"]
    nodes = collect_documented_nodes(tree)
    errors: list[str] = []
    for symbol in sorted(required_symbols):
        errors.extend(validate_symbol(path, symbol, nodes))
    return errors


def check_repository(root: Path = REPOSITORY_ROOT) -> list[str]:
    """检查仓库注释策略，位于 Agent-Harness 本地验证和 CI 发布门禁入口。"""
    errors: list[str] = []
    for relative_path, symbols in CURRENT_REQUIRED_SYMBOLS.items():
        path = root / relative_path
        if not path.is_file():
            errors.append(f"{relative_path}: 文件不存在")
            continue
        errors.extend(check_python_file(path, symbols))

    harness_root = root / "harness"
    if harness_root.is_dir():
        for path in sorted(harness_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            nodes = collect_documented_nodes(tree)
            errors.extend(check_python_file(path, public_harness_symbols(nodes)))
    return errors


def main() -> int:
    """运行中文注释检查，位于 Agent-Harness 工程门禁命令行入口。"""
    errors = check_repository()
    if errors:
        print("Agent-Harness 中文注释检查失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Agent-Harness 中文注释检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
