"""验证中文注释门禁，位于 Agent-Harness 本地测试和 CI 执行流程。"""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.check_harness_comments import check_repository


class HarnessCommentPolicyTests(unittest.TestCase):
    """确保 Harness 基础设施新增代码持续说明中文职责和流程位置。"""

    def test_required_harness_symbols_have_chinese_context(self) -> None:
        """在测试流程中执行注释策略，防止缺少设计意图的代码进入 Harness。"""
        repository_root = Path(__file__).resolve().parents[1]
        errors = check_repository(repository_root)
        self.assertEqual([], errors, "\n".join(errors))


if __name__ == "__main__":
    unittest.main()
