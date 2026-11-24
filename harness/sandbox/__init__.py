"""Agent-Harness 单 Sandbox 执行基础设施。

该包位于 Tool Router 与具体容器运行时之间，定义 Sandbox 生命周期、Docker 隔离实现和
Manager；正式路径不提供宿主进程回退，Docker 不可用时返回基础设施错误。
"""

from .base import (
    SandboxExecRequest,
    SandboxExecResult,
    SandboxHandle,
    SandboxInfo,
    SandboxRuntime,
    SandboxSpec,
)
from .docker_runtime import DockerSandboxRuntime
from .manager import SandboxArtifactManifest, SandboxManager

__all__ = [
    "DockerSandboxRuntime",
    "SandboxExecRequest",
    "SandboxExecResult",
    "SandboxArtifactManifest",
    "SandboxHandle",
    "SandboxInfo",
    "SandboxManager",
    "SandboxRuntime",
    "SandboxSpec",
]
