"""Agent-Harness 三个 MVP 基础工具实现。

该模块位于 Tool Router 与 SandboxManager 之间，把 read_file、write_file 和 shell_exec
转换为容器内 argv；所有路径均限制为相对 workspace，任何命令都不在宿主机执行。
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from harness.sandbox import SandboxExecRequest, SandboxManager

from .contracts import PolicyDecision, ToolCall, ToolDefinition, ToolResult, ToolStatus


class BasicToolExecutor:
    """执行三个基础工具，位于 Harness Router 授权后到 Sandbox 的唯一实现边界。"""

    def __init__(self, sandbox_manager: SandboxManager):
        self._sandboxes = sandbox_manager

    def execute(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        policy: PolicyDecision,
        sandbox_id: str,
    ) -> ToolResult:
        """将 ToolCall 转为容器内执行，位于 Harness Policy allow 后的执行环节。"""
        if call.tool_name == "read_file":
            return self._read_file(call, definition, policy, sandbox_id)
        if call.tool_name == "write_file":
            return self._write_file(call, definition, policy, sandbox_id)
        if call.tool_name == "shell_exec":
            return self._shell_exec(call, definition, policy, sandbox_id)
        return self._failure(call, policy, "unknown_tool", "未实现的工具")

    def _read_file(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        policy: PolicyDecision,
        sandbox_id: str,
    ) -> ToolResult:
        relative = self._safe_relative(str(call.arguments["path"]))
        script = (
            "import pathlib,sys; root=pathlib.Path('/workspace').resolve(); "
            "target=(root/sys.argv[1]).resolve(); target.relative_to(root); "
            "sys.stdout.buffer.write(target.read_bytes())"
        )
        raw = self._sandboxes.execute(
            sandbox_id,
            SandboxExecRequest(
                ("python", "-c", script, relative),
                definition.timeout_seconds,
                definition.max_output_bytes,
            ),
        )
        failure = self._process_failure(call, policy, raw)
        if failure is not None:
            return failure
        return ToolResult(
            call.call_id,
            call.tool_name,
            ToolStatus.SUCCEEDED,
            policy,
            output={
                "path": relative,
                "content": raw.stdout.decode("utf-8", errors="replace"),
                "sha256": hashlib.sha256(raw.stdout).hexdigest(),
            },
            metadata={"duration_ms": raw.duration_ms, "bytes": len(raw.stdout)},
        )

    def _write_file(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        policy: PolicyDecision,
        sandbox_id: str,
    ) -> ToolResult:
        relative = self._safe_relative(str(call.arguments["path"]))
        content = str(call.arguments["content"]).encode("utf-8")
        if len(content) > 10 * 1024 * 1024:
            return self._failure(call, policy, "file_too_large", "写入内容超过 10 MiB")
        script = (
            "import pathlib,sys; root=pathlib.Path('/workspace').resolve(); "
            "target=(root/sys.argv[1]).resolve(); target.relative_to(root); "
            "target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(sys.stdin.buffer.read())"
        )
        raw = self._sandboxes.execute(
            sandbox_id,
            SandboxExecRequest(
                ("python", "-c", script, relative),
                definition.timeout_seconds,
                definition.max_output_bytes,
                stdin=content,
            ),
        )
        failure = self._process_failure(call, policy, raw)
        if failure is not None:
            return failure
        return ToolResult(
            call.call_id,
            call.tool_name,
            ToolStatus.SUCCEEDED,
            policy,
            output={
                "path": relative,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            metadata={"duration_ms": raw.duration_ms},
        )

    def _shell_exec(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        policy: PolicyDecision,
        sandbox_id: str,
    ) -> ToolResult:
        argv = tuple(str(item) for item in call.arguments["argv"])
        if any("\x00" in item for item in argv):
            return self._failure(call, policy, "invalid_arguments", "argv 包含空字符")
        raw = self._sandboxes.execute(
            sandbox_id,
            SandboxExecRequest(argv, definition.timeout_seconds, definition.max_output_bytes),
        )
        failure = self._process_failure(call, policy, raw, nonzero_is_failure=False)
        if failure is not None:
            return failure
        status = ToolStatus.SUCCEEDED if raw.exit_code == 0 else ToolStatus.FAILED
        return ToolResult(
            call.call_id,
            call.tool_name,
            status,
            policy,
            output={
                "exit_code": raw.exit_code,
                "stdout": raw.stdout.decode("utf-8", errors="replace"),
                "stderr": raw.stderr.decode("utf-8", errors="replace"),
            },
            error_code=None if raw.exit_code == 0 else "command_failed",
            error_message=None if raw.exit_code == 0 else "Sandbox 命令返回非零状态",
            metadata={"duration_ms": raw.duration_ms},
        )

    def _safe_relative(self, value: str) -> str:
        if not value or "\\" in value or "\x00" in value:
            raise ValueError("workspace path must be a non-empty POSIX relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("workspace path escapes the Sandbox root")
        return path.as_posix()

    def _process_failure(
        self,
        call: ToolCall,
        policy: PolicyDecision,
        raw,
        *,
        nonzero_is_failure: bool = True,
    ) -> ToolResult | None:
        if raw.timed_out:
            return ToolResult(
                call.call_id,
                call.tool_name,
                ToolStatus.TIMED_OUT,
                policy,
                error_code="tool_timeout",
                error_message="Sandbox 工具执行超时，容器已终止",
                metadata={"duration_ms": raw.duration_ms},
            )
        if raw.output_limit_exceeded:
            return self._failure(call, policy, "output_limit_exceeded", "工具输出超过上限")
        if nonzero_is_failure and raw.exit_code != 0:
            return self._failure(
                call,
                policy,
                "tool_execution_failed",
                raw.stderr.decode("utf-8", errors="replace")[:1000],
            )
        return None

    def _failure(
        self,
        call: ToolCall,
        policy: PolicyDecision,
        code: str,
        message: str,
    ) -> ToolResult:
        return ToolResult(
            call.call_id,
            call.tool_name,
            ToolStatus.FAILED,
            policy,
            error_code=code,
            error_message=message,
        )
