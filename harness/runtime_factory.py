"""Agent-Harness 生产运行时装配工厂。

该模块位于 GraphFlow/CLI 入口与 Harness 各基础设施实现之间，集中装配 Controller、
Model Gateway、Tool Router、Docker Sandbox、Workspace、Validator 与真实 Node Handler。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from scheduler.path_security import resolve_within

from .agent_loop import AgentLoop
from .controller import HarnessController
from .contracts import AgentProfileRef
from .integration import AgentProfile, AgentProfileStore
from .integration.graphflow_handler import HarnessNodeHandler
from .models import ModelGateway, ModelProvider, OpenAIResponsesAdapter
from .sandbox import DockerSandboxRuntime, SandboxManager, SandboxRuntime
from .task_runner import HarnessTaskRunner
from .tools import PolicyEngine, ToolRouter, build_mvp_registry
from .tools.ledger import ToolLedger
from .validation import DeterministicValidator
from .workspace import WorkspaceManager


@dataclass(frozen=True)
class RuntimeConfig:
    """描述正式 Harness 装配参数，位于环境变量到运行时组件之间的配置边界。"""

    docker_image: str = "python:3.12-alpine"
    model_endpoint: str = "https://api.openai.com/v1/responses"
    allow_network: bool = False
    require_image_digest: bool = False
    profile_bindings: str = ".loop/harness/agent-bindings.json"

    @classmethod
    def from_environment(cls) -> "RuntimeConfig":
        """从环境读取显式配置，位于 Harness 进程启动时的生产参数加载环节。"""
        return cls(
            docker_image=os.environ.get("HARNESS_DOCKER_IMAGE", cls.docker_image),
            model_endpoint=os.environ.get("HARNESS_MODEL_ENDPOINT", cls.model_endpoint),
            allow_network=os.environ.get("HARNESS_ALLOW_NETWORK", "false").lower() == "true",
            require_image_digest=os.environ.get("HARNESS_REQUIRE_IMAGE_DIGEST", "false").lower() == "true",
            profile_bindings=os.environ.get("HARNESS_PROFILE_BINDINGS", cls.profile_bindings),
        )


@dataclass(frozen=True)
class HarnessRuntime:
    """保存已装配组件，位于 GraphFlow、CLI 与恢复 Worker 的共享运行时边界。"""

    controller: HarnessController
    runner: HarnessTaskRunner
    handler: HarnessNodeHandler
    profile_resolver: Callable[[str], AgentProfile]
    sandboxes: SandboxManager


class RuntimeFactory:
    """构建单 Task/Agent/Sandbox 生产链路，位于 Harness 应用 Composition Root。"""

    def __init__(
        self,
        root: Path,
        *,
        config: RuntimeConfig | None = None,
        provider: ModelProvider | None = None,
        sandbox_runtime: SandboxRuntime | None = None,
    ) -> None:
        self.root = root.resolve()
        self.config = config or RuntimeConfig.from_environment()
        self._provider = provider
        self._sandbox_runtime = sandbox_runtime

    def build(self) -> HarnessRuntime:
        """装配真实 Harness，位于 GraphFlow execute 和 CLI task-run 的共同启动环节。"""
        if self.config.require_image_digest and "@sha256:" not in self.config.docker_image:
            raise RuntimeError("HARNESS_DOCKER_IMAGE must use an immutable sha256 digest")
        controller = HarnessController(self.root)
        provider = self._provider or self._openai_provider()
        sandbox_runtime = self._sandbox_runtime or DockerSandboxRuntime(
            self.root,
            allow_network=self.config.allow_network,
        )
        sandboxes = SandboxManager(self.root, sandbox_runtime, image=self.config.docker_image)
        router = ToolRouter(
            build_mvp_registry(),
            PolicyEngine(),
            ToolLedger(self.root),
            sandboxes,
            platform_tools=frozenset({"read_file", "write_file", "shell_exec"}),
            event_store=controller.event_store,
        )
        runner = HarnessTaskRunner(
            self.root,
            controller,
            AgentLoop(controller, ModelGateway(provider), router),
            DeterministicValidator(router),
            sandboxes,
            workspace_manager=WorkspaceManager(self.root),
        )
        resolver = self._profile_resolver()
        return HarnessRuntime(
            controller,
            runner,
            HarnessNodeHandler(self.root, runner, resolver),
            resolver,
            sandboxes,
        )

    def _openai_provider(self) -> ModelProvider:
        """创建正式模型出口，位于 RuntimeFactory 的凭据 fail-closed 边界。"""
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for production Harness")
        return OpenAIResponsesAdapter(api_key, endpoint=self.config.model_endpoint)

    def _profile_resolver(self) -> Callable[[str], AgentProfile]:
        """加载 GraphFlow Agent 名称绑定，位于节点身份到不可变 Profile 的解析环节。"""
        path = resolve_within(self.root, *self.config.profile_bindings.replace("\\", "/").split("/"))
        store = AgentProfileStore(self.root)

        def resolve(agent_name: str) -> AgentProfile:
            if not path.is_file():
                raise RuntimeError(f"Harness agent binding file is missing: {self.config.profile_bindings}")
            payload = json.loads(path.read_text(encoding="utf-8"))
            binding = payload.get(agent_name)
            if not isinstance(binding, dict):
                raise RuntimeError(f"Harness agent binding is missing: {agent_name}")
            return store.load(AgentProfileRef.from_dict(binding))

        return resolve

