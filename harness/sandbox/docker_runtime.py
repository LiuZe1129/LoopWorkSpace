"""Agent-Harness Docker Sandbox Runtime。

该模块位于 Sandbox 接口与 Docker CLI 之间，使用参数数组创建默认断网、只读根文件系统、
非 root、无 Linux capabilities 且受 CPU/内存/PID 限制的单 Attempt 容器。
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from harness.errors import InfrastructureUnavailableError

from .base import SandboxExecRequest, SandboxExecResult, SandboxHandle, SandboxInfo, SandboxSpec


@dataclass(frozen=True)
class CommandOutcome:
    """描述 Docker CLI 进程结果，位于 Harness Runtime 的有界输出执行边界。"""

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    output_limit_exceeded: bool
    duration_ms: int


class CommandRunner(Protocol):
    """抽象 Docker CLI 启动器，位于 Harness Runtime 与操作系统进程之间的测试边界。"""

    def run(
        self,
        argv: list[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        stdin: bytes | None = None,
    ) -> CommandOutcome:
        """执行有界命令，位于 Harness Docker 操作的底层进程控制环节。"""
        ...


class BoundedCommandRunner:
    """限制 Docker CLI 时间和输出，位于 Harness 防止控制进程资源失控的边界。"""

    def run(
        self,
        argv: list[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        stdin: bytes | None = None,
    ) -> CommandOutcome:
        """运行无 shell 子进程，位于 Harness Docker CLI 的安全执行环节。"""
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise InfrastructureUnavailableError("Docker CLI is unavailable") from exc
        output = {"stdout": bytearray(), "stderr": bytearray()}
        total = 0
        lock = threading.Lock()
        overflow = threading.Event()

        def read_stream(name: str, stream) -> None:
            nonlocal total
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                with lock:
                    remaining = max(0, max_output_bytes - total)
                    output[name].extend(chunk[:remaining])
                    total += len(chunk)
                    if total > max_output_bytes:
                        overflow.set()

        readers = [
            threading.Thread(target=read_stream, args=("stdout", process.stdout), daemon=True),
            threading.Thread(target=read_stream, args=("stderr", process.stderr), daemon=True),
        ]
        for reader in readers:
            reader.start()

        def write_input() -> None:
            if process.stdin is None:
                return
            try:
                process.stdin.write(stdin or b"")
                process.stdin.close()
            except (BrokenPipeError, OSError):
                return

        writer = threading.Thread(target=write_input, daemon=True)
        writer.start()
        timed_out = False
        while process.poll() is None:
            if overflow.is_set():
                process.kill()
                break
            if time.monotonic() - started >= timeout_seconds:
                timed_out = True
                process.kill()
                break
            time.sleep(0.01)
        process.wait()
        writer.join(timeout=1)
        for reader in readers:
            reader.join(timeout=1)
        return CommandOutcome(
            exit_code=process.returncode,
            stdout=bytes(output["stdout"]),
            stderr=bytes(output["stderr"]),
            timed_out=timed_out,
            output_limit_exceeded=overflow.is_set(),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


class DockerSandboxRuntime:
    """实现单 Attempt Docker 容器，位于 Harness 正式 Tool 执行的隔离边界。"""

    def __init__(
        self,
        allowed_root: Path,
        *,
        runner: CommandRunner | None = None,
        docker_binary: str = "docker",
        allow_network: bool = False,
    ) -> None:
        self._allowed_root = allowed_root.resolve()
        self._runner = runner or BoundedCommandRunner()
        self._docker_binary = docker_binary
        self._allow_network = allow_network

    def is_available(self) -> bool:
        """探测 Docker Engine，位于 Harness 启动自检和基础设施错误分类环节。"""
        try:
            outcome = self._runner.run(
                [self._docker_binary, "version", "--format", "{{.Server.Version}}"],
                timeout_seconds=10,
                max_output_bytes=4096,
            )
        except InfrastructureUnavailableError:
            return False
        return outcome.exit_code == 0

    def create(self, spec: SandboxSpec) -> SandboxHandle:
        """创建受限容器，位于 Harness Attempt 启动前的 Sandbox 准备环节。"""
        workspace = spec.workspace.resolve()
        self._assert_allowed(workspace)
        if "," in str(workspace):
            raise ValueError("workspace path cannot contain comma for Docker mount syntax")
        if spec.network_enabled and not self._allow_network:
            raise ValueError("Sandbox network is disabled by platform policy")
        workspace.mkdir(parents=True, exist_ok=True)
        container_name = f"loop-{self._stable_name(spec.sandbox_id)}"
        network = "bridge" if spec.network_enabled else "none"
        argv = [
            self._docker_binary,
            "run",
            "--detach",
            "--pull",
            "never",
            "--name",
            container_name,
            "--label",
            f"loop.sandbox_id={spec.sandbox_id}",
            "--log-driver",
            "none",
            "--network",
            network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--pids-limit",
            str(spec.pids_limit),
            "--memory",
            spec.memory_limit,
            "--memory-swap",
            spec.memory_limit,
            "--cpus",
            str(spec.cpu_limit),
            "--user",
            spec.user,
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,src={workspace},dst=/workspace",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
        ]
        for index, source in enumerate(spec.readonly_inputs):
            resolved = source.resolve()
            self._assert_allowed(resolved)
            argv.extend(["--mount", f"type=bind,src={resolved},dst=/inputs/{index},readonly"])
        argv.extend([spec.image, "sh", "-c", "trap : TERM INT; sleep infinity & wait"])
        outcome = self._runner.run(argv, timeout_seconds=30, max_output_bytes=65_536)
        if outcome.exit_code != 0:
            message = outcome.stderr.decode("utf-8", errors="replace")[:1000]
            raise InfrastructureUnavailableError(f"Docker Sandbox create failed: {message}")
        runtime_id = outcome.stdout.decode("utf-8", errors="replace").strip() or container_name
        return SandboxHandle(spec.sandbox_id, spec.attempt_id, runtime_id, workspace)

    def exec(self, handle: SandboxHandle, request: SandboxExecRequest) -> SandboxExecResult:
        """执行容器内 argv，位于 Harness Tool 获得授权后的唯一命令执行环节。"""
        if not request.argv:
            raise ValueError("Sandbox argv must not be empty")
        argv = [self._docker_binary, "exec", "--workdir", "/workspace"]
        for key, value in sorted(request.environment.items()):
            argv.extend(["--env", f"{key}={value}"])
        if request.stdin is not None:
            argv.append("--interactive")
        argv.append(handle.runtime_id)
        argv.extend(request.argv)
        outcome = self._runner.run(
            argv,
            timeout_seconds=request.timeout_seconds,
            max_output_bytes=request.max_output_bytes,
            stdin=request.stdin,
        )
        if outcome.timed_out or outcome.output_limit_exceeded:
            # 仅终止 Docker CLI 不足以保证容器内进程消失，因此直接 kill 整个 Attempt 容器。
            self.cancel(handle)
        return SandboxExecResult(
            outcome.exit_code,
            outcome.stdout,
            outcome.stderr,
            outcome.timed_out,
            outcome.output_limit_exceeded,
            outcome.duration_ms,
        )

    def cancel(self, handle: SandboxHandle) -> None:
        """强制终止容器，位于 Harness Tool 超时和 Task 取消传播的执行环节。"""
        outcome = self._runner.run(
            [self._docker_binary, "kill", handle.runtime_id],
            timeout_seconds=15,
            max_output_bytes=16_384,
        )
        if outcome.exit_code != 0:
            raise InfrastructureUnavailableError("Docker failed to kill Sandbox container")

    def destroy(self, handle: SandboxHandle) -> None:
        """强制移除容器，位于 Harness 收集 Artifact 后的 Sandbox 回收环节。"""
        outcome = self._runner.run(
            [self._docker_binary, "rm", "--force", handle.runtime_id],
            timeout_seconds=30,
            max_output_bytes=16_384,
        )
        if outcome.exit_code != 0:
            raise InfrastructureUnavailableError("Docker failed to remove Sandbox container")

    def inspect(self, handle: SandboxHandle) -> SandboxInfo:
        """查询容器状态，位于 Harness 恢复判断和运维诊断环节。"""
        outcome = self._runner.run(
            [self._docker_binary, "inspect", "--format", "{{.State.Status}}", handle.runtime_id],
            timeout_seconds=10,
            max_output_bytes=4096,
        )
        status = "missing" if outcome.exit_code != 0 else outcome.stdout.decode("utf-8", errors="replace").strip()
        return SandboxInfo(handle.sandbox_id, handle.runtime_id, status)

    def _assert_allowed(self, path: Path) -> None:
        try:
            path.relative_to(self._allowed_root)
        except ValueError as exc:
            raise ValueError(f"Sandbox mount escapes allowed root: {path}") from exc

    def _stable_name(self, sandbox_id: str) -> str:
        import hashlib

        return hashlib.sha256(sandbox_id.encode("utf-8")).hexdigest()[:16]
