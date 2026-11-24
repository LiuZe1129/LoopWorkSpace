"""Agent-Harness 真实 Docker Sandbox 可选冒烟测试。

默认 CI 不假设存在 Docker；设置 ``LOOP_RUN_DOCKER_SMOKE=1`` 后，本测试要求 Docker
Engine 与预拉取镜像可用，并现场验证超时会终止整个 Attempt 容器而非只结束 CLI。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from harness.sandbox import DockerSandboxRuntime, SandboxExecRequest, SandboxManager


@unittest.skipUnless(
    os.environ.get("LOOP_RUN_DOCKER_SMOKE") == "1",
    "需要显式设置 LOOP_RUN_DOCKER_SMOKE=1 并预拉取 Sandbox 镜像",
)
class HarnessDockerSmokeTests(unittest.TestCase):
    """验证真实容器终止，位于 Harness 阶段 3 的环境级隔离验收环节。"""

    def test_timeout_kills_real_attempt_container(self) -> None:
        """执行超时进程并检查容器退出，确认 Harness Sandbox 实际终止目标进程。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = DockerSandboxRuntime(root)
            self.assertTrue(runtime.is_available(), "Docker Engine 不可用")
            image = os.environ.get("LOOP_SANDBOX_IMAGE", "python:3.12-alpine")
            manager = SandboxManager(root, runtime, image=image)
            handle = manager.create("exec-smoke", "attempt-smoke")
            try:
                result = manager.execute(
                    handle.sandbox_id,
                    SandboxExecRequest(
                        ("python", "-c", "import time; time.sleep(60)"),
                        timeout_seconds=0.5,
                        max_output_bytes=4096,
                    ),
                )
                self.assertTrue(result.timed_out)
                self.assertEqual(manager.inspect(handle.sandbox_id).status, "exited")
            finally:
                manager.destroy(handle.sandbox_id)


if __name__ == "__main__":
    unittest.main()
