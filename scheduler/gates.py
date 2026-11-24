from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .state import DoneItem


@dataclass
class GateCheckResult:
    id: str
    check: str
    expect: str
    actual: str
    exit_code: int
    passed: bool


def evaluate_expect(expect: str, stdout: str, exit_code: int) -> bool:
    text = stdout.strip()
    if expect.startswith("exitcode:"):
        expected = expect.split(":", 1)[1]
        if expected == "nonzero":
            return exit_code != 0
        return exit_code == int(expected)
    if expect.startswith("regex:"):
        return re.search(expect.split(":", 1)[1], text) is not None
    if expect.startswith("contains:"):
        return expect.split(":", 1)[1] in text
    if expect.startswith(">="):
        try:
            return float(text) >= float(expect[2:].strip())
        except ValueError:
            return False
    return text == expect


def run_check(
    root: Path,
    item: DoneItem,
    timeout_seconds: int = 300,
    *,
    allow_host_shell: bool = False,
) -> GateCheckResult:
    """执行确定性验收命令。

    该函数位于 GraphFlow Gate 到 Harness Tool/Sandbox 的迁移边界。正式流程默认
    禁止宿主 Shell；只有测试显式启用 mock 时才保留旧执行路径。
    """
    if not allow_host_shell:
        return GateCheckResult(
            item.id,
            item.check,
            item.expect,
            "host shell execution disabled; sandbox required",
            126,
            False,
        )
    # 旧宿主执行仅用于 GraphFlow mock 回归测试；正式 Harness 流程必须改走 Sandbox。
    completed = subprocess.run(
        item.check,
        cwd=root,
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    actual = stdout.strip() or stderr.strip()
    passed = evaluate_expect(item.expect, stdout, completed.returncode)
    return GateCheckResult(item.id, item.check, item.expect, actual, completed.returncode, passed)

