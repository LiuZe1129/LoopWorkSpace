"""Agent-Harness Sandbox 稳定接口。

该模块位于 Tool Router 与容器实现的依赖反转边界，定义单 Attempt Sandbox 的创建、
执行、取消、销毁和检查契约，禁止上层直接调用 Docker 或宿主 Shell。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SandboxSpec:
    """描述 Sandbox 隔离配置，位于 Harness Attempt 准备执行环境的流程环节。"""

    sandbox_id: str
    attempt_id: str
    workspace: Path
    image: str = "python:3.12-alpine"
    network_enabled: bool = False
    cpu_limit: float = 1.0
    memory_limit: str = "512m"
    pids_limit: int = 64
    user: str = "65532:65532"
    readonly_inputs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class SandboxHandle:
    """引用已创建容器，位于 Harness Manager 复用单 Attempt Sandbox 的边界。"""

    sandbox_id: str
    attempt_id: str
    runtime_id: str
    workspace: Path


@dataclass(frozen=True)
class SandboxExecRequest:
    """描述一次容器内进程请求，位于 Harness Tool 实现到 Sandbox Runtime 的边界。"""

    argv: tuple[str, ...]
    timeout_seconds: float
    max_output_bytes: int
    stdin: bytes | None = None
    environment: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxExecResult:
    """描述容器内进程结果，位于 Harness Sandbox 返回 Tool Router 的流程环节。"""

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_limit_exceeded: bool = False
    duration_ms: int = 0


@dataclass(frozen=True)
class SandboxInfo:
    """描述 Sandbox 当前状态，位于 Harness 运维检查和恢复判断的只读边界。"""

    sandbox_id: str
    runtime_id: str
    status: str


class SandboxRuntime(Protocol):
    """抽象 Sandbox 运行时，位于 Harness Manager 与 Docker 等实现之间。"""

    def create(self, spec: SandboxSpec) -> SandboxHandle:
        """创建 Sandbox，位于 Harness Attempt 启动前的隔离环境准备环节。"""
        ...

    def exec(self, handle: SandboxHandle, request: SandboxExecRequest) -> SandboxExecResult:
        """执行容器内命令，位于 Harness Tool 获得 Policy allow 后的执行环节。"""
        ...

    def cancel(self, handle: SandboxHandle) -> None:
        """终止 Sandbox 内进程，位于 Harness Tool 超时或 Task 取消的传播环节。"""
        ...

    def destroy(self, handle: SandboxHandle) -> None:
        """销毁 Sandbox，位于 Harness Attempt 终态和资源回收环节。"""
        ...

    def inspect(self, handle: SandboxHandle) -> SandboxInfo:
        """检查 Sandbox，位于 Harness 恢复和孤儿容器诊断环节。"""
        ...
