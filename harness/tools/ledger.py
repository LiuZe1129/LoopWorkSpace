"""Agent-Harness Tool 副作用 Ledger。

该模块位于 Policy allow 与 Sandbox 执行前后，以 idempotency_key 记录 started、completed
和 ambiguous；服务重启遇到未完成副作用时禁止盲目重放。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from scheduler.path_security import resolve_within, safe_child

from harness.contracts import utc_now
from harness.errors import LedgerConflictError
from harness.persistence.atomic import _atomic_write_json, _exclusive_lock, _read_json

from .contracts import ToolCall, ToolResult


class LedgerStatus(str, Enum):
    """描述 Tool 副作用提交状态，位于 Harness 崩溃恢复和幂等判断流程。"""

    STARTED = "started"
    COMPLETED = "completed"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class LedgerRecord:
    """记录一次幂等 ToolCall，位于 Harness Router 执行副作用的恢复边界。"""

    idempotency_key: str
    call_fingerprint: str
    call_id: str
    tool_name: str
    status: LedgerStatus
    result: ToolResult | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        """序列化 Ledger 记录，供 Harness 服务重启后判定重放安全性。"""
        return {
            "idempotency_key": self.idempotency_key,
            "call_fingerprint": self.call_fingerprint,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "result": None if self.result is None else self.result.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LedgerRecord":
        """恢复 Ledger 记录，位于 Harness 幂等命中和不确定副作用处置环节。"""
        result = payload.get("result")
        return cls(
            idempotency_key=str(payload["idempotency_key"]),
            call_fingerprint=str(payload["call_fingerprint"]),
            call_id=str(payload["call_id"]),
            tool_name=str(payload["tool_name"]),
            status=LedgerStatus(str(payload["status"])),
            result=None if result is None else ToolResult.from_dict(result),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )


class ToolLedger:
    """管理 ToolCall 幂等记录，位于 Harness Policy 与实际 Sandbox 副作用之间。"""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.executions_dir = resolve_within(self.root, ".loop", "harness", "executions")

    def begin(self, call: ToolCall, *, safe_to_replay: bool = False) -> LedgerRecord:
        """登记 started 或标记遗留调用 ambiguous，位于 Harness Tool 执行之前。"""
        path = self._record_path(call)
        fingerprint = self._fingerprint(call)
        with _exclusive_lock(path):
            if path.is_file():
                current = LedgerRecord.from_dict(_read_json(path))
                self._assert_same_call(current, call, fingerprint)
                if current.status == LedgerStatus.STARTED:
                    if safe_to_replay:
                        restarted = LedgerRecord(
                            current.idempotency_key,
                            current.call_fingerprint,
                            call.call_id,
                            current.tool_name,
                            LedgerStatus.STARTED,
                            None,
                            current.created_at,
                            utc_now(),
                        )
                        _atomic_write_json(path, restarted.to_dict())
                        return restarted
                    ambiguous = LedgerRecord(
                        current.idempotency_key,
                        current.call_fingerprint,
                        current.call_id,
                        current.tool_name,
                        LedgerStatus.AMBIGUOUS,
                        None,
                        current.created_at,
                        utc_now(),
                    )
                    _atomic_write_json(path, ambiguous.to_dict())
                    return ambiguous
                return current
            now = utc_now()
            record = LedgerRecord(
                call.idempotency_key,
                fingerprint,
                call.call_id,
                call.tool_name,
                LedgerStatus.STARTED,
                None,
                now,
                now,
            )
            _atomic_write_json(path, record.to_dict())
            return record

    def complete(self, call: ToolCall, result: ToolResult) -> LedgerRecord:
        """提交 ToolResult，位于 Harness Sandbox 执行完成后的幂等提交环节。"""
        path = self._record_path(call)
        fingerprint = self._fingerprint(call)
        with _exclusive_lock(path):
            current = LedgerRecord.from_dict(_read_json(path))
            self._assert_same_call(current, call, fingerprint)
            completed = LedgerRecord(
                current.idempotency_key,
                current.call_fingerprint,
                current.call_id,
                current.tool_name,
                LedgerStatus.COMPLETED,
                result,
                current.created_at,
                utc_now(),
            )
            _atomic_write_json(path, completed.to_dict())
            return completed

    def inspect(self, call: ToolCall) -> LedgerRecord | None:
        """读取幂等记录，位于 Harness 恢复诊断和 ToolCall 审计的只读环节。"""
        path = self._record_path(call)
        if not path.is_file():
            return None
        record = LedgerRecord.from_dict(_read_json(path))
        self._assert_same_call(record, call, self._fingerprint(call))
        return record

    def list_records(self, execution_id: str, attempt_id: str) -> list[LedgerRecord]:
        """列出 Attempt Ledger，位于 Harness Reconciler 检查未完成副作用的流程环节。"""
        execution_dir = safe_child(self.executions_dir, execution_id, "execution_id")
        attempts_dir = resolve_within(execution_dir, "attempts")
        attempt_dir = safe_child(attempts_dir, attempt_id, "attempt_id")
        ledger_dir = resolve_within(attempt_dir, "tool-ledger")
        if not ledger_dir.is_dir():
            return []
        return [LedgerRecord.from_dict(_read_json(path)) for path in sorted(ledger_dir.glob("*.json"))]

    def mark_ambiguous(self, call: ToolCall) -> LedgerRecord:
        """标记副作用结果不确定，位于 Harness Sandbox 异常中断后的禁止重放环节。"""
        path = self._record_path(call)
        fingerprint = self._fingerprint(call)
        with _exclusive_lock(path):
            current = LedgerRecord.from_dict(_read_json(path))
            self._assert_same_call(current, call, fingerprint)
            ambiguous = LedgerRecord(
                current.idempotency_key,
                current.call_fingerprint,
                current.call_id,
                current.tool_name,
                LedgerStatus.AMBIGUOUS,
                None,
                current.created_at,
                utc_now(),
            )
            _atomic_write_json(path, ambiguous.to_dict())
            return ambiguous

    def abandon(self, call: ToolCall) -> None:
        """撤销只读 started 记录，位于 Harness 无副作用工具失败后的安全重试环节。"""
        path = self._record_path(call)
        fingerprint = self._fingerprint(call)
        with _exclusive_lock(path):
            current = LedgerRecord.from_dict(_read_json(path))
            self._assert_same_call(current, call, fingerprint)
            if current.status != LedgerStatus.STARTED:
                raise LedgerConflictError("only a started read-only ToolCall can be abandoned")
            path.unlink()

    def _record_path(self, call: ToolCall) -> Path:
        execution_dir = safe_child(self.executions_dir, call.execution_id, "execution_id")
        attempts_dir = resolve_within(execution_dir, "attempts")
        attempt_dir = safe_child(attempts_dir, call.attempt_id, "attempt_id")
        ledger_dir = resolve_within(attempt_dir, "tool-ledger")
        digest = hashlib.sha256(call.idempotency_key.encode("utf-8")).hexdigest()
        return resolve_within(ledger_dir, f"{digest}.json")

    def _fingerprint(self, call: ToolCall) -> str:
        stable = json.dumps(
            {"tool_name": call.tool_name, "arguments": call.arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"sha256:{hashlib.sha256(stable.encode('utf-8')).hexdigest()}"

    def _assert_same_call(self, record: LedgerRecord, call: ToolCall, fingerprint: str) -> None:
        if record.call_fingerprint != fingerprint or record.tool_name != call.tool_name:
            raise LedgerConflictError(
                f"idempotency key reused with different ToolCall: {call.idempotency_key}"
            )
