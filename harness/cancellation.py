"""Agent-Harness 协作式取消信号。

该模块位于 Controller 与 Agent Loop/Model/Tool 之间，为单进程运行提供线程安全的即时
取消检查；Controller 同时持久化请求并调用 Sandbox.cancel，保证重启后仍能看到取消意图。
"""

from __future__ import annotations

import threading

from .errors import CancellationRequestedError


class CancellationToken:
    """传递线程安全取消信号，位于 Harness 长耗时模型和 Tool 操作的协作边界。"""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason = "cancelled"

    def cancel(self, reason: str = "cancelled") -> None:
        """发出取消信号，位于 Harness Controller 接收用户取消请求的即时传播环节。"""
        self._reason = reason
        self._event.set()

    def is_cancelled(self) -> bool:
        """查询取消状态，位于 Harness Agent Loop 每个安全边界的轮询环节。"""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """在已取消时中断流程，位于 Harness 外部调用前后的强制停止边界。"""
        if self._event.is_set():
            raise CancellationRequestedError(self._reason)

    def wait(self, timeout_seconds: float) -> bool:
        """等待取消或超时，位于 Harness Model Gateway 监听活动供应商请求的流程环节。"""
        return self._event.wait(timeout_seconds)

    @property
    def reason(self) -> str:
        """返回取消原因，供 Harness 终态事件和 TaskResult 生成流程使用。"""
        return self._reason
