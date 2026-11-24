"""验证 Agent-Harness 阶段 2 的 Profile、Context、Model Gateway 与脱敏基础设施。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.context_manager import ContextManager
from harness.contracts import Task, TaskBudget, TaskPermissions
from harness.errors import AgentProfileIntegrityError, ContextBudgetError
from harness.integration import AgentProfileStore, build_agent_profile
from harness.models import (
    ModelError,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    OpenAIResponsesAdapter,
)
from harness.observability import ModelEventRecorder, redact_value
from harness.persistence import EventStore


OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status", "summary"],
    "properties": {
        "status": {"type": "string", "enum": ["success", "failed"]},
        "summary": {"type": "string"},
    },
    "additionalProperties": False,
}


class SequenceProvider:
    """按序返回结果或错误，位于 Harness Model Gateway 有限重试的测试边界。"""

    def __init__(self, outcomes: list[ModelResponse | Exception]):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.timeouts: list[float] = []

    def complete(self, request: ModelRequest, *, timeout_seconds: float) -> ModelResponse:
        """模拟单次供应商调用，供 Harness Gateway 测试重试和超时参数传递。"""
        self.calls += 1
        self.timeouts.append(timeout_seconds)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class HarnessStageTwoTests(unittest.TestCase):
    """覆盖阶段 2 基础设施，确认 Harness 模型调用可版本化、受预算且可审计。"""

    def setUp(self) -> None:
        """创建隔离目录，位于 Harness Profile 持久化测试的准备环节。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        """回收隔离目录，位于 Harness 阶段 2 测试的收尾环节。"""
        self.temporary.cleanup()

    def make_profile(self, *, max_input_tokens: int = 1000):
        """构造版本化 Profile，位于 Harness Context 和 Gateway 测试输入环节。"""
        return build_agent_profile(
            agent_id="backend-dev",
            version="1.0.0",
            prompt="你是后端开发 Agent，只输出 JSON。",
            model="test-model",
            max_input_tokens=max_input_tokens,
            max_output_tokens=200,
            temperature=0.2,
            tools=(),
            capabilities=("structured_task",),
            output_schema=OUTPUT_SCHEMA,
        )

    def make_task(self, profile=None) -> Task:
        """构造绑定 Profile 的 Task，位于 GraphFlow 到 Harness 模型上下文测试边界。"""
        selected = profile or self.make_profile()
        return Task(
            task_id="task-1",
            graph_run_id="run-1",
            node_id="node-1",
            objective="返回结构化执行摘要",
            agent_profile=selected.to_ref(),
            permissions=TaskPermissions(()),
            budget=TaskBudget(max_model_tokens=1000),
            acceptance=({"type": "schema", "required": ["status"]},),
            version_set={"spec": "v1"},
        )

    def make_response(self, text: str, *, response_id: str = "resp-1") -> ModelResponse:
        """构造模型响应，位于 Harness Gateway 结构校验与用量累计测试环节。"""
        return ModelResponse(
            response_id=response_id,
            model="test-model",
            text=text,
            finish_reason="completed",
            usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )

    def test_profile_store_round_trip_and_hash_tamper_detection(self) -> None:
        """验证 Profile 版本闭包，确保 Harness Prompt 被篡改后无法加载。"""
        profile = self.make_profile()
        store = AgentProfileStore(self.root)
        store.register(profile)
        self.assertEqual(store.load(profile.to_ref()), profile)
        prompt_path = self.root / ".loop" / "harness" / "agents" / "backend-dev" / "1.0.0" / "prompt.md"
        prompt_path.write_text("已被篡改", encoding="utf-8")
        with self.assertRaises(AgentProfileIntegrityError):
            store.load(profile.to_ref())

    def test_existing_markdown_agent_can_be_migrated(self) -> None:
        """验证旧 Markdown 可迁移，确保 GraphFlow Agent 定义能进入 Harness Profile 流程。"""
        source = Path(__file__).resolve().parents[1] / "agents" / "backend-dev.md"
        store = AgentProfileStore(self.root)
        profile = store.migrate_markdown(
            source,
            agent_id="backend-dev",
            version="legacy-v1",
            model="test-model",
            max_input_tokens=20_000,
            max_output_tokens=16_000,
        )
        self.assertIn("BackendDevAgent", store.load(profile.to_ref()).prompt)

    def test_context_manager_trims_oldest_optional_messages(self) -> None:
        """验证确定性裁剪，确保 Harness 保留 Profile 与 Task 并优先丢弃旧历史。"""
        profile = self.make_profile(max_input_tokens=160)
        task = self.make_task(profile)
        history = (
            ModelMessage("assistant", "旧消息" * 100),
            ModelMessage("assistant", "较新消息"),
        )
        window = ContextManager().build(task, profile, history=history)
        self.assertGreaterEqual(window.dropped_messages, 1)
        self.assertEqual(window.messages[0].role, "system")
        self.assertIn(task.objective, window.messages[1].content)
        self.assertLessEqual(window.estimated_input_tokens, profile.max_input_tokens)

    def test_context_manager_rejects_required_context_over_budget(self) -> None:
        """验证基础上下文超限失败，确保 Harness 不通过裁掉 Task 或 Prompt 掩盖预算问题。"""
        profile = self.make_profile(max_input_tokens=5)
        with self.assertRaises(ContextBudgetError):
            ContextManager().build(self.make_task(profile), profile)

    def test_context_manager_reserves_output_from_remaining_task_budget(self) -> None:
        """验证剩余总预算约束，确保 Harness 历史用量和本轮输出预留不会被忽略。"""
        profile = self.make_profile(max_input_tokens=1000)
        task = self.make_task(profile)
        with self.assertRaises(ContextBudgetError):
            ContextManager().build(task, profile, model_tokens_used=900)

    def test_context_manager_rejects_wrong_profile(self) -> None:
        """验证 Task/Profile 引用一致性，确保 Harness 不会运行错误版本的 Agent。"""
        task_profile = self.make_profile()
        other = build_agent_profile(
            agent_id="backend-dev",
            version="2.0.0",
            prompt="另一个 Prompt",
            model="test-model",
            max_input_tokens=1000,
            max_output_tokens=200,
        )
        with self.assertRaises(AgentProfileIntegrityError):
            ContextManager().build(self.make_task(task_profile), other)

    def test_gateway_retries_invalid_schema_and_accumulates_usage(self) -> None:
        """验证无效结构有限重试，确保 Harness 不会重置已消耗的模型预算。"""
        provider = SequenceProvider(
            [
                self.make_response('{"status":"success"}', response_id="resp-1"),
                self.make_response('{"status":"success","summary":"完成"}', response_id="resp-2"),
            ]
        )
        records: list[dict] = []
        gateway = ModelGateway(provider, max_attempts=2, timeout_seconds=7, recorder=records.append, sleeper=lambda _: None)
        result = gateway.invoke(self.make_request(), output_schema=OUTPUT_SCHEMA)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.total_usage.total_tokens, 30)
        self.assertEqual(result.structured_output["summary"], "完成")
        self.assertEqual(provider.timeouts, [7, 7])
        self.assertEqual([item["status"] for item in records], ["failed", "succeeded"])
        self.assertEqual(records[0]["usage"]["total_tokens"], 15)

    def test_gateway_stops_after_retry_limit(self) -> None:
        """验证模型错误有限重试，确保 Harness 供应商故障不会形成无限循环。"""
        provider = SequenceProvider(
            [
                ModelError("provider_unavailable", "temporary", retryable=True),
                ModelError("provider_unavailable", "temporary", retryable=True),
            ]
        )
        gateway = ModelGateway(provider, max_attempts=2, sleeper=lambda _: None)
        with self.assertRaises(ModelError) as raised:
            gateway.invoke(self.make_request())
        self.assertEqual(raised.exception.attempts, 2)
        self.assertEqual(provider.calls, 2)

    def test_openai_adapter_maps_request_without_exposing_key(self) -> None:
        """验证 Responses Adapter 映射，确保 Harness API Key 仅进入授权 Header。"""
        captured: dict = {}

        def transport(endpoint, payload, headers, timeout):
            captured.update({"endpoint": endpoint, "payload": payload, "headers": headers, "timeout": timeout})
            return {
                "id": "resp-1",
                "model": "test-model",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "完成"}]}],
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            }

        adapter = OpenAIResponsesAdapter(
            "sk-super-secret",
            transport=transport,
            input_cost_per_million=2.0,
            output_cost_per_million=8.0,
        )
        response = adapter.complete(self.make_request(), timeout_seconds=9)
        self.assertEqual(response.text, "完成")
        self.assertEqual(captured["timeout"], 9)
        self.assertNotIn("sk-super-secret", str(captured["payload"]))
        self.assertIn("Bearer", captured["headers"]["Authorization"])
        self.assertAlmostEqual(response.usage.cost or 0.0, 0.000022)

    def test_redaction_removes_nested_credentials(self) -> None:
        """验证递归脱敏，确保 Harness 日志不包含 API Key、Bearer Token 或密码。"""
        value = {
            "api_key": "sk-secret-value",
            "nested": {"Authorization": "Bearer abc.def", "message": "key=sk-abcdefghijk"},
            "password": "plain",
        }
        redacted = redact_value(value)
        serialized = str(redacted)
        self.assertNotIn("sk-secret", serialized)
        self.assertNotIn("abc.def", serialized)
        self.assertNotIn("plain", serialized)

    def test_model_event_recorder_persists_safe_usage_summary(self) -> None:
        """验证模型审计事件，确保 Harness 时间线记录版本和用量但不泄露密钥。"""
        store = EventStore(self.root)
        recorder = ModelEventRecorder(store, "exec-1", "attempt-1")
        recorder.record(
            {
                "model": "test-model",
                "duration_ms": 12,
                "usage": {"total_tokens": 15, "cost": 0.01},
                "request_metadata": {"profile_hash": "sha256:abc", "api_key": "sk-secret-value"},
            }
        )
        event = store.read("exec-1")[0]
        self.assertEqual(event.event_type, "model.call")
        self.assertEqual(event.payload["usage"]["total_tokens"], 15)
        self.assertNotIn("sk-secret-value", str(event.payload))

    def test_model_only_structured_task_flow(self) -> None:
        """验证单 Agent 无工具闭环，确保 Harness Context 可经 Gateway 返回结构化结果。"""
        profile = self.make_profile()
        context_manager = ContextManager()
        window = context_manager.build(self.make_task(profile), profile)
        provider = SequenceProvider([self.make_response('{"status":"success","summary":"已完成"}')])
        request = context_manager.build_request(window, profile, request_id="request-integrated")
        result = ModelGateway(provider, sleeper=lambda _: None).invoke(request, output_schema=profile.output_schema)
        self.assertEqual(result.structured_output, {"status": "success", "summary": "已完成"})
        self.assertEqual(request.metadata["profile_hash"], profile.content_hash)

    def make_request(self) -> ModelRequest:
        """构造模型请求，位于 Harness Gateway 和 Provider Adapter 的测试入口。"""
        return ModelRequest(
            request_id="request-1",
            model="test-model",
            messages=(ModelMessage("system", "只输出 JSON"), ModelMessage("user", "完成任务")),
            max_output_tokens=100,
            temperature=0.2,
        )


if __name__ == "__main__":
    unittest.main()
