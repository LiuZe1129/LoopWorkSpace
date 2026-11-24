"""Agent-Harness 版本化 AgentProfile。

该模块位于现有 ``agents/*.md`` 与 Context Manager 之间，把 Markdown Prompt、模型参数、
能力和输出 Schema 固化为不可变版本，并在每次加载时校验内容哈希。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scheduler.path_security import resolve_within, safe_child

from harness.contracts import AgentProfileRef
from harness.errors import AgentProfileIntegrityError, AlreadyExistsError, NotFoundError
from harness.persistence.atomic import _atomic_write_json, _atomic_write_text, _exclusive_lock, _read_json


@dataclass(frozen=True)
class AgentProfile:
    """描述不可变 Agent 行为版本，位于 Harness Task 绑定模型能力和 Prompt 的边界。"""

    agent_id: str
    version: str
    content_hash: str
    prompt: str
    model: str
    max_input_tokens: int
    max_output_tokens: int
    temperature: float | None = None
    tools: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    output_schema: dict[str, Any] | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def to_ref(self) -> AgentProfileRef:
        """生成 Profile 引用，供 Harness Task Contract 固定 Agent 行为版本。"""
        return AgentProfileRef(self.agent_id, self.version, self.content_hash)

    def to_dict(self) -> dict[str, Any]:
        """序列化 Profile 元数据，供 Harness 与独立 Prompt 文件共同持久化。"""
        return {
            "schema_version": "1.0",
            "agent_id": self.agent_id,
            "version": self.version,
            "content_hash": self.content_hash,
            "prompt_ref": "prompt.md",
            "model": self.model,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "tools": list(self.tools),
            "capabilities": list(self.capabilities),
            "output_schema": self.output_schema,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any], prompt: str) -> "AgentProfile":
        """恢复 Profile，位于 Harness Context Manager 加载版本化 Agent 的流程环节。"""
        return cls(
            agent_id=str(payload["agent_id"]),
            version=str(payload["version"]),
            content_hash=str(payload["content_hash"]),
            prompt=prompt,
            model=str(payload["model"]),
            max_input_tokens=int(payload["max_input_tokens"]),
            max_output_tokens=int(payload["max_output_tokens"]),
            temperature=None if payload.get("temperature") is None else float(payload["temperature"]),
            tools=tuple(str(item) for item in payload.get("tools", []) or []),
            capabilities=tuple(str(item) for item in payload.get("capabilities", []) or []),
            output_schema=payload.get("output_schema"),
            metadata={str(key): str(value) for key, value in (payload.get("metadata", {}) or {}).items()},
        )


def build_agent_profile(
    *,
    agent_id: str,
    version: str,
    prompt: str,
    model: str,
    max_input_tokens: int,
    max_output_tokens: int,
    temperature: float | None = None,
    tools: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = (),
    output_schema: dict[str, Any] | None = None,
    metadata: dict[str, str] | None = None,
) -> AgentProfile:
    """构建带内容哈希的 Profile，位于 Harness 迁移 Markdown Agent 的版本固化环节。"""
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    if max_input_tokens <= 0 or max_output_tokens <= 0:
        raise ValueError("token limits must be positive")
    values = {
        "agent_id": agent_id,
        "version": version,
        "prompt": prompt,
        "model": model,
        "max_input_tokens": max_input_tokens,
        "max_output_tokens": max_output_tokens,
        "temperature": temperature,
        "tools": list(tools),
        "capabilities": list(capabilities),
        "output_schema": output_schema,
        "metadata": dict(metadata or {}),
    }
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    content_hash = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    return AgentProfile(
        agent_id=agent_id,
        version=version,
        content_hash=content_hash,
        prompt=prompt,
        model=model,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        tools=tools,
        capabilities=capabilities,
        output_schema=output_schema,
        metadata=dict(metadata or {}),
    )


class AgentProfileStore:
    """持久化版本化 AgentProfile，位于 Harness 配置发布与运行时加载的边界。"""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.agents_dir = resolve_within(self.root, ".loop", "harness", "agents")

    def register(self, profile: AgentProfile) -> AgentProfile:
        """登记不可变 Profile，位于 Harness Agent 配置发布到运行时之前。"""
        self._verify_hash(profile)
        directory = self._profile_dir(profile.agent_id, profile.version)
        profile_path = resolve_within(directory, "profile.json")
        with _exclusive_lock(profile_path):
            if profile_path.exists():
                raise AlreadyExistsError(
                    f"AgentProfile already exists: {profile.agent_id}@{profile.version}"
                )
            # 先写 Prompt、最后发布 profile.json；加载方只把 profile.json 视为提交标记。
            _atomic_write_text(resolve_within(directory, "prompt.md"), profile.prompt)
            _atomic_write_json(profile_path, profile.to_dict())
        return profile

    def migrate_markdown(
        self,
        source_path: Path,
        *,
        agent_id: str,
        version: str,
        model: str,
        max_input_tokens: int,
        max_output_tokens: int,
        temperature: float | None = None,
        tools: tuple[str, ...] = (),
        capabilities: tuple[str, ...] = (),
        output_schema: dict[str, Any] | None = None,
    ) -> AgentProfile:
        """迁移 Markdown Agent，位于 GraphFlow 旧定义进入 Harness Profile 的适配流程。"""
        prompt = source_path.read_text(encoding="utf-8")
        profile = build_agent_profile(
            agent_id=agent_id,
            version=version,
            prompt=prompt,
            model=model,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            tools=tools,
            capabilities=capabilities,
            output_schema=output_schema,
            metadata={"source": source_path.as_posix()},
        )
        return self.register(profile)

    def load(self, reference: AgentProfileRef) -> AgentProfile:
        """加载并校验 Profile，位于 Harness Execution 构建模型上下文之前。"""
        directory = self._profile_dir(reference.agent_id, reference.version)
        profile_path = resolve_within(directory, "profile.json")
        prompt_path = resolve_within(directory, "prompt.md")
        if not profile_path.is_file() or not prompt_path.is_file():
            raise NotFoundError(f"AgentProfile not found: {reference.agent_id}@{reference.version}")
        profile = AgentProfile.from_dict(
            _read_json(profile_path),
            prompt_path.read_text(encoding="utf-8"),
        )
        self._verify_hash(profile)
        if profile.content_hash != reference.content_hash:
            raise AgentProfileIntegrityError("AgentProfile reference hash does not match stored content")
        return profile

    def _profile_dir(self, agent_id: str, version: str) -> Path:
        agent_dir = safe_child(self.agents_dir, agent_id, "agent_id")
        return safe_child(agent_dir, version, "agent version")

    def _verify_hash(self, profile: AgentProfile) -> None:
        rebuilt = build_agent_profile(
            agent_id=profile.agent_id,
            version=profile.version,
            prompt=profile.prompt,
            model=profile.model,
            max_input_tokens=profile.max_input_tokens,
            max_output_tokens=profile.max_output_tokens,
            temperature=profile.temperature,
            tools=profile.tools,
            capabilities=profile.capabilities,
            output_schema=profile.output_schema,
            metadata=profile.metadata,
        )
        if rebuilt.content_hash != profile.content_hash:
            raise AgentProfileIntegrityError("AgentProfile content hash verification failed")
