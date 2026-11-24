"""Agent-Harness 确定性结果验证器。

该模块位于 Agent 完成提议与 TaskResult 成功终态之间，验证最终输出 Schema、
文件断言和 Sandbox 命令；模型自述完成不能绕过这些确定性检查。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .contracts import Task, utc_now
from .integration import AgentProfile
from .schema_validation import validate_json_schema
from .tools import ToolResult, ToolRouter, ToolStatus


@dataclass(frozen=True)
class ValidationCheck:
    """描述单项确定性验收，位于 Harness Validator 到结果证据的边界。"""

    check_id: str
    check_type: str
    passed: bool
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化检查结果，供 Harness ValidationReport 与 Evidence 持久化。"""
        return {
            "check_id": self.check_id,
            "type": self.check_type,
            "passed": self.passed,
            "message": self.message,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class ValidationReport:
    """汇总确定性验收结论，位于 Harness 完成提议与成功终态之间。"""

    status: str
    checks: tuple[ValidationCheck, ...]
    tool_calls_used: int
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """序列化报告，供 ResultStore 和 GraphFlow Evidence 使用。"""
        return {
            "schema_version": "1.0",
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
            "tool_calls_used": self.tool_calls_used,
            "created_at": self.created_at,
        }


class DeterministicValidator:
    """执行确定性完成检查，位于 Agent Loop 完成后、TaskResult 成功前。"""

    def __init__(self, tool_router: ToolRouter):
        self._tools = tool_router

    def validate(
        self,
        task: Task,
        profile: AgentProfile,
        completion: dict[str, Any],
        *,
        execution_id: str,
        attempt_id: str,
        sandbox_id: str,
    ) -> ValidationReport:
        """验证输出与 acceptance，位于 VALIDATING_RESULT 状态的执行环节。"""
        checks: list[ValidationCheck] = []
        tool_calls_used = 0
        if profile.output_schema is not None:
            errors = validate_json_schema(completion, profile.output_schema)
            checks.append(ValidationCheck(
                "completion-schema",
                "output_schema",
                not errors,
                "完成输出符合 Schema" if not errors else "; ".join(errors),
            ))
        else:
            checks.append(ValidationCheck(
                "completion-nonempty",
                "output",
                bool(completion),
                "完成提议非空" if completion else "完成提议不能为空",
            ))
        for index, acceptance in enumerate(task.acceptance):
            check, used = self._run_acceptance(
                task,
                profile,
                completion,
                acceptance,
                execution_id=execution_id,
                attempt_id=attempt_id,
                sandbox_id=sandbox_id,
                index=index,
            )
            checks.append(check)
            tool_calls_used += used
        status = "passed" if checks and all(check.passed for check in checks) else "failed"
        return ValidationReport(status, tuple(checks), tool_calls_used)

    def _run_acceptance(
        self,
        task: Task,
        profile: AgentProfile,
        completion: dict[str, Any],
        acceptance: dict[str, Any],
        *,
        execution_id: str,
        attempt_id: str,
        sandbox_id: str,
        index: int,
    ) -> tuple[ValidationCheck, int]:
        check_type = str(acceptance.get("type", ""))
        check_id = str(acceptance.get("id", f"acceptance-{index + 1}"))
        if check_type in {"file_exists", "file_contains"}:
            path = str(acceptance.get("path", ""))
            result = self._route(
                task, profile, execution_id, attempt_id, sandbox_id,
                "read_file", {"path": path}, check_id,
            )
            passed = result.status == ToolStatus.SUCCEEDED
            message = "文件存在且可读" if passed else (result.error_message or "文件读取失败")
            if passed and check_type == "file_contains":
                expected = str(acceptance.get("text", ""))
                passed = expected in str((result.output or {}).get("content", ""))
                message = "文件包含期望文本" if passed else "文件不包含期望文本"
            return ValidationCheck(
                check_id,
                check_type,
                passed,
                message,
                {
                    "call_id": result.call_id,
                    "path": path,
                    "sha256": (result.output or {}).get("sha256"),
                },
            ), 1
        if check_type == "command":
            result = self._route(
                task, profile, execution_id, attempt_id, sandbox_id,
                "shell_exec", {"argv": acceptance.get("argv", [])}, check_id,
            )
            expected_exit = int(acceptance.get("expected_exit_code", 0))
            actual_exit = (result.output or {}).get("exit_code")
            passed = result.status not in {
                ToolStatus.DENIED, ToolStatus.TIMED_OUT, ToolStatus.AMBIGUOUS,
            }
            if acceptance.get("expected_exit_nonzero"):
                passed = passed and actual_exit not in {None, 0}
            else:
                passed = passed and actual_exit == expected_exit
            stdout = str((result.output or {}).get("stdout", "")).strip()
            if "stdout_contains" in acceptance:
                passed = passed and str(acceptance["stdout_contains"]) in stdout
            if "stdout_regex" in acceptance:
                try:
                    passed = passed and re.search(str(acceptance["stdout_regex"]), stdout) is not None
                except re.error:
                    passed = False
            if "stdout_equals" in acceptance:
                passed = passed and stdout == str(acceptance["stdout_equals"])
            if "stdout_min" in acceptance:
                try:
                    passed = passed and float(stdout) >= float(acceptance["stdout_min"])
                except ValueError:
                    passed = False
            return ValidationCheck(
                check_id,
                check_type,
                passed,
                "命令验收通过" if passed else (result.error_message or "命令验收失败"),
                {
                    "call_id": result.call_id,
                    "exit_code": actual_exit,
                    "expected_exit_code": expected_exit,
                    "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
                },
            ), 1
        if check_type == "output_schema":
            schema = acceptance.get("schema")
            errors = (
                validate_json_schema(completion, schema)
                if isinstance(schema, dict)
                else ["acceptance.schema 必须是对象"]
            )
            return ValidationCheck(
                check_id,
                check_type,
                not errors,
                "额外输出 Schema 验收通过" if not errors else "; ".join(errors),
            ), 0
        return ValidationCheck(
            check_id,
            check_type or "unknown",
            False,
            "不支持的 acceptance 类型",
        ), 0

    def _route(
        self,
        task: Task,
        profile: AgentProfile,
        execution_id: str,
        attempt_id: str,
        sandbox_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        check_id: str,
    ) -> ToolResult:
        stable = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(f"{check_id}:{tool_name}:{stable}".encode("utf-8")).hexdigest()
        return self._tools.route(
            task,
            profile,
            execution_id=execution_id,
            attempt_id=attempt_id,
            sandbox_id=sandbox_id,
            tool_name=tool_name,
            arguments=arguments,
            idempotency_key=f"validator-{digest}",
        )
