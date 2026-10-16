from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    condition: str = "always"


# Mode A (SPEC先行) 节点集：对齐 scheduler/drg.py:MANDATORY_GOVERNANCE_NODES
# 删除 11 个 Mode B 遗留节点（spec_node / assignment_node / architecture_node /
# architecture_review_node / env_check_node / product_shape_node / ui_design_node /
# ui_review_node / development_subgraph / final_review_node / memory_sediment_node）
# 补充 design_spec_node + design_gate_node（Design Spec P0/P1 已实现）
#
# 节点分类：
#   - 主链路节点（14个）：由 drg.py:build_execution_graph() 纳入执行图，
#     executor.run() 通过 ready_nodes() 串行调度。
#   - 状态转换节点（4个）：retry_node / awaiting_human_node /
#     awaiting_spec_change_node / escalated_node。
#     这些节点 NOT 纳入执行图（drg.py 不构建它们），而是由 executor
#     的状态机处理：
#       retry_node          ← RetryPolicyRunner.handle_failure() 副作用
#       awaiting_spec_change_node ← executor.await_spec_change() 副作用
#       awaiting_human_node ← executor.await_human_review() 副作用
#       escalated_node     ← RetryPolicyRunner（重试耗尽）或
#                            resume_human_review(REJECT) 设置终态
#     恢复路径（非图边，由显式 CLI 命令触发）：
#       resume-run          ← resume_spec_change() 从 AWAITING_SPEC_CHANGE 恢复
#       resume-human-review ← resume_human_review() 从 AWAITING_HUMAN 恢复
GRAPHFLOW_NODES = [
    "spec_binding_node",
    "planning_node",
    "plan_gate_node",
    "architecture_impact_node",
    "architecture_gate_node",
    "design_spec_node",
    "design_gate_node",
    "drg_compile_node",
    "implementation_drg_node",
    "final_gate_node",
    "conformance_node",
    "awaiting_spec_change_node",
    "delivery_review_node",
    "deploy_node",
    "done_node",
    "retry_node",
    "awaiting_human_node",
    "escalated_node",
]

# Mode A 边集：严格对齐 scheduler/drg.py:build_execution_graph 主链路
# retry_node 目标由 retry_policy.py 动态决定，静态边只画到 escalated_node
GRAPHFLOW_EDGES = [
    # 主链路
    Edge("spec_binding_node", "planning_node", "snapshot=status:PUBLISHED and hash=match"),
    Edge("planning_node", "plan_gate_node", "execution-plan.json generated"),
    Edge("plan_gate_node", "architecture_impact_node", "plan_gate=PASS"),
    Edge("architecture_impact_node", "architecture_gate_node", "architecture.json generated"),
    Edge("architecture_gate_node", "design_spec_node", "gate=PASS"),
    Edge("design_spec_node", "design_gate_node", "design-spec.json generated"),
    Edge("design_gate_node", "drg_compile_node", "gate=PASS"),
    Edge("drg_compile_node", "implementation_drg_node", "execution-graph.json validated"),
    Edge("implementation_drg_node", "final_gate_node", "all_drg_nodes_done"),
    Edge("final_gate_node", "conformance_node", "gate=PASS"),
    Edge("conformance_node", "delivery_review_node", "conformance=PASS"),
    Edge("delivery_review_node", "deploy_node", "review=APPROVE"),
    Edge("deploy_node", "done_node", "deploy.success=true"),
    # 失败 / 规格变更分支
    Edge("plan_gate_node", "awaiting_spec_change_node", "plan_gate=SPEC_MISMATCH"),
    Edge("plan_gate_node", "retry_node", "plan_gate=FAIL"),
    Edge("architecture_gate_node", "retry_node", "gate=FAIL"),
    Edge("design_gate_node", "retry_node", "gate=FAIL"),
    Edge("final_gate_node", "retry_node", "gate=FAIL"),
    Edge("conformance_node", "awaiting_spec_change_node", "conformance=SPEC_MISMATCH"),
    Edge("delivery_review_node", "retry_node", "review=REJECT and retry_count < max_retry"),
    Edge("delivery_review_node", "escalated_node", "review=REJECT and retry_count >= max_retry"),
    Edge("delivery_review_node", "awaiting_human_node", "review=L3_REVIEW_REQUIRED"),
    # 终态 / 恢复
    Edge("retry_node", "escalated_node", "retry_count >= max_retry"),
    Edge("awaiting_spec_change_node", "spec_binding_node", "spec_change applied"),
    Edge("awaiting_human_node", "spec_binding_node", "human=APPROVE and previous=SPEC_CHANGE"),
    Edge("awaiting_human_node", "escalated_node", "human=REJECT or timeout"),
]


def describe_graph() -> str:
    lines = ["GraphFlow nodes:"]
    lines.extend(f"- {node}" for node in GRAPHFLOW_NODES)
    lines.append("")
    lines.append("GraphFlow edges:")
    lines.extend(f"- {edge.source} -> {edge.target} [{edge.condition}]" for edge in GRAPHFLOW_EDGES)
    return "\n".join(lines)
