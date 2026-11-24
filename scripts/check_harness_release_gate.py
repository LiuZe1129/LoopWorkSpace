"""检查 Agent-Harness MVP 发布门禁。

该脚本位于 CI 合并/发布之前，验证 12 个 Golden Cases、P0 安全覆盖、正式入口不引用
Fake/Mock Runtime，并扫描 Harness 包中是否出现 ``shell=True`` 宿主执行旁路。
"""

from __future__ import annotations

import re
import sys
import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
# 本地源码门禁在尚未安装包时也必须可运行；仅把已解析的仓库根加入模块搜索路径。
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from harness.evaluation import load_golden_cases


REQUIRED_CASES = {f"G-{index:02d}" for index in range(1, 13)}
REQUIRED_P0 = {"G-03", "G-04", "G-05", "G-06", "G-09", "G-10", "G-11", "G-12"}


def check_release_gate(root: Path = REPOSITORY_ROOT) -> list[str]:
    """返回发布阻断项，位于 Harness Golden Cases 与 CI 发布决策之间。"""
    errors: list[str] = []
    cases_path = root / "tests" / "golden" / "cases.json"
    try:
        cases = load_golden_cases(cases_path)
    except (OSError, ValueError) as exc:
        return [f"无法加载 Golden Cases: {exc}"]
    by_id = {case.case_id: case for case in cases}
    missing = sorted(REQUIRED_CASES - by_id.keys())
    if missing:
        errors.append(f"缺少 Golden Cases: {', '.join(missing)}")
    incorrect_p0 = sorted(case_id for case_id in REQUIRED_P0 if case_id in by_id and by_id[case_id].priority != "P0")
    if incorrect_p0:
        errors.append(f"安全用例未标记为 P0: {', '.join(incorrect_p0)}")
    for case in cases:
        if case.priority == "P0" and case.mode != "mock":
            errors.append(f"P0 用例必须可在离线 CI 重复运行: {case.case_id}")
        if "status" not in case.expected:
            errors.append(f"Golden Case 缺少期望状态: {case.case_id}")

    coverage_path = root / "tests" / "golden" / "coverage.json"
    try:
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"无法加载 Golden Case 测试映射: {exc}")
        coverage = {}
    for case_id in sorted(REQUIRED_CASES):
        targets = coverage.get(case_id, [])
        if not targets:
            errors.append(f"Golden Case 未绑定可执行测试: {case_id}")
            continue
        for target in targets:
            relative, separator, method = str(target).partition("::")
            path = root / relative
            if not separator or not method or not path.is_file():
                errors.append(f"Golden Case 测试映射无效: {case_id} -> {target}")
                continue
            if f"def {method}(" not in path.read_text(encoding="utf-8"):
                errors.append(f"Golden Case 测试方法不存在: {case_id} -> {target}")

    shell_pattern = re.compile(r"\bshell\s*=\s*True\b")
    for path in sorted((root / "harness").rglob("*.py")):
        if shell_pattern.search(path.read_text(encoding="utf-8")):
            errors.append(f"Harness 正式包禁止 shell=True: {path.relative_to(root).as_posix()}")
    production_files = [
        root / "harness" / "cli.py",
        root / "harness" / "task_runner.py",
        root / "harness" / "integration" / "graphflow_handler.py",
    ]
    for path in production_files:
        content = path.read_text(encoding="utf-8")
        if "from tests" in content or "FakeSandbox" in content or "MockSandbox" in content:
            errors.append(f"正式入口引用测试 Runtime: {path.relative_to(root).as_posix()}")
    return errors


def main() -> int:
    """执行发布检查，位于 Agent-Harness CI 单测之后的最终安全门禁环节。"""
    errors = check_release_gate()
    if errors:
        print("Agent-Harness 发布门禁失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Agent-Harness 发布门禁通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
