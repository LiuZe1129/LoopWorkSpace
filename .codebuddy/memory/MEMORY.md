# Memory

## Project: Spec-Driven GraphFlow Development Factory

Repository at `d:\01Loop-WorkSpace` — Spec + GraphFlow architecture for spec-driven software development. Docs in `doc/`, runtime in `scheduler/`, agent definitions in `agents/`.

**Four layers**: Spec Layer (immutable Published SpecSnapshot) → GraphFlow Layer (GraphRun + DRG) → Handler Layer (pluggable node handlers) → Evidence Layer (Conformance + EvidenceManifest). Plus ToolContract cross-cutting.

> 2026-09-01 精简：去除 dahua-client 子项目 SDK 真机修复细节（与主项目 Spec-Driven GraphFlow 不直接相关），合并 Skills 子项详情到 daily log。

## Key Implementation Facts

- Python 3.12 via `py -3` (system default is Python 2.7, unusable)
- Run tests: `py -3 -m unittest tests.test_scheduler_runtime -v`
- CLI: `py -3 -m scheduler.main <command>`
- Runs stored in `.loop/runs/{run_id}/`; Specs in `specs/{project}/published/{spec_id}/v{version}/spec-snapshot.json`
- 14 mandatory governance nodes in DRG (`scheduler/drg.py:MANDATORY_GOVERNANCE_NODES`), serial execution (no concurrency)
- `smoke-flow` command: publish → create-run → plan → compile-graph → tool-contract → execute → evidence
- DRG fully serial (ready[0] only); ContextPack includes all completed artifacts (linear growth); dhnetsdk.h triggers C4828 warnings (suppressed via >nul)
- **Mode A 清理完成（2026-09-01）**：系统采用纯 Mode A（SPEC 先行），SpecAgent 在独立 SPEC 层执行产出 Snapshot，DRG 从 `spec_binding_node` 消费。Mode B 遗留（spec_node 等 11 个节点）已从 `scheduler/graph.py` / `agents/_schema.md` / `doc/graphflow-system-operation.md` / `tests/test_scheduler_runtime.py` 清理。`graph.py:GRAPHFLOW_NODES` 与 `drg.py:MANDATORY_GOVERNANCE_NODES` 对齐（14 治理节点 + 4 辅助节点）。
- **P0 执行链路修复完成（2026-09-03）**：漏洞2（幽灵节点→graph.py 注释对齐）、漏洞3（SpecChange 恢复→`runs.py:rebind_spec` + `executor.py:resume_spec_change` + CLI `resume-run`）、漏洞4（L3 人工审查→`executor.py:await_human_review` + `resume_human_review` + CLI `resume-human-review`）。
- **P1/P2 漏洞修复完成（2026-09-03）**：漏洞1（双重状态机→`runs.py:RUN_LEGAL_TRANSITIONS` Mode A 转换表 + `state.py:LEGAL_TRANSITIONS` 遗留标记）、漏洞5（闸门空检查→`gate_auto.py` 为 `plan_gate_node`/`architecture_gate_node` 增加实质性文件存在性检查）、漏洞6（done_node 双重定义→`executor.py:execute_done_node` 写 `terminal_marker:True` 最小 artifact，DONE 逻辑统一由 `run()` 后置检查处理）、漏洞7（无超时熔断→`DRGExecutor.__init__` 增加 `wall_clock_timeout_seconds` 参数，`run()` 循环检查超时→FAILED）。
- **P2 轻量级监督修复完成（2026-09-03）**：V-8（SpecChange 恢复循环断路器→`_check_run_health()` 读 `events.jsonl` 中 `spec_rebound` 计数，≥3→FAILED，`MAX_SPEC_REBOUND_COUNT=3`）、V-9（wall_clock_timeout 非累积→`_check_run_health()` 读首/尾事件时间戳算累积时间，仅 `has_prior_execution` 时触发）、V-10（execute_node 异常→try/except 主防线 + stuck 节点检测二级防线）、V-11（重试不继续→CLI `execute` while 循环复用 executor 实例）。`_check_run_health()` 在 `run()` 和 `resume_spec_change()` 开头调用。
- 三层质检：L1 自动闸门（gate-auto shell check）+ L2 契约评审（ReviewAgent）+ L3 人工抽查（awaiting_human_node 从 delivery_review 进入）

## Architecture Invariants (opt-in philosophy)

Each feature is gated by an opt-in flag so deterministic test runtime (stub agents) stays green. Detailed field constraints live in `scheduler/spec_flow.py:validate_change_spec_payload` and `scheduler/specs.py` / `scheduler/schema.py`.

- **1.1 Strict Spec**: `schema_version:"1.1"` → EARS requirement + GWT acceptance + M:N mapping + type conditions. 1.0 backward compatible.
- **huaDesign contract**: `design_system:"huaDesign"` → token dot-path refs, single primary color, 4n spacing. Core: `scheduler/schema.py:validate_ui_design_contract`.
- **Intent audit**: `intent_audit.py` returns NO_INTENT_DOCS when no documentation dir → no findings.
- **Design Spec P0**: `design_spec_node` + `design_gate_node` between architecture_gate and drg_compile. Minimal fallback generates valid design-spec.json.
- **Design Spec P1**: `drg_compile_node` refines plan from rich design spec → recompiles graph → executor hot-reloads. Minimal fallback does NOT trigger refine. Core: `scheduler/plans.py:refine_plan_from_design_spec`.
- **PM Discussion**: `pm_discussion` field in ChangeSpec → create-prd (必选) + interview-script/summarize-interview/summarize-meeting (可选). Core: `scheduler/spec_flow.py`. Agent: `agents/spec-agent.md` §PM Discussion 阶段.
- **Discovery & Assumptions**: `discovery` field + structured `assumptions[]` (with `category` field) → opportunity-solution-tree + identify-assumptions-new/existing + prioritize-assumptions (all optional). Agent: `agents/spec-agent.md` §Discovery & Assumptions 阶段.
- **Job Story**: `job_story` field in Requirement → situation + motivation + outcome (When...I want...so I can...). Complements EARS. Agent: `agents/spec-agent.md` §Job Stories 阶段.

## 固化 Skills (architecture-level)

- **shipping-artifacts** → architect-agent: 5 intent docs to `documentation/`
- **intended-vs-implemented** → `intent_audit.py` + `conformance.py` (deterministic) + review-agent delivery_review_node (semantic). GAP_FOUND ≠ spec_change.
- **pre-mortem** → architect-agent risks: category(tiger/paper_tiger/elephant) + urgency(launch_blocking/fast_follow/track)
- **test-scenarios** → test-agent test-report.json: test_scenarios array per acceptance
- **PM Discussion 4 skill** (2026-09-01 固化): create-prd / interview-script / summarize-interview / summarize-meeting. 详见 daily 2026-09-01.md.
- **Discovery & Assumptions 4 skill** (2026-09-01 固化): opportunity-solution-tree / identify-assumptions-new / identify-assumptions-existing / prioritize-assumptions. 详见 daily 2026-09-01.md.
- **job-stories** (2026-09-01 固化): When [situation], I want [motivation], so I can [outcome] + acceptance criteria. 详见 daily 2026-09-01.md.

SpecAgent 共固化 9 个 skill: PM Discussion 4 + Discovery & Assumptions 4 + Job Stories 1.

## Requirement/Acceptance 1.1

- Requirement = EARS (actor + condition + system_response) + type + priority (MUST/SHOULD/MAY)
- Acceptance = GWT (given + when + then) + requirement_ids (M:N) + type (automated/manual/review/performance/security)
- Mapping completeness: no orphan Requirement, no orphan Acceptance
- ExecutionPlan 1.1: groups WP by requirement_ids array; WP has requirement_type/priority/tags/implementation_focus/planning_basis
- Conformance: trace_matrix field; three-state PASS/UNVERIFIED/FAIL; overall priority-aware (MUST blocks, SHOULD/MAY don't); no empty-acceptance auto-PASS

## Test Count

123 tests. PM Discussion / Discovery / Job Story opt-in 校验均不破坏存量测试. P0 恢复路径修复新增 6 测试（SpecChange resume + L3 human review pause/resume）. P1/P2 漏洞修复新增 6 测试（状态转换表 + 闸门检查 + 超时熔断 + done_node 标记）. P2 轻量级监督修复新增 5 测试（V-8 spec_rebound 断路器 + V-9 累积超时 + V-10 stuck 节点 + V-10 异常路由 + V-11 续行）.

## dahua-client MVP 项目（projects/dahua-client/）

Qt5 Widget 项目（VS 2017 x64 + Qt 5.14.0 msvc2017_64 + NetSDK V3.5.1）。骨架：CMakeLists + src/main.cpp + src/core/{NetSdkWrapper,LoginParams} + src/widgets/{LoginDialog,MainWindow,DeviceTreeView,PreviewWidget,PreviewGridWidget} + src/resources/{resources.qrc,styles/dahua_client.qss} + tests/.

**测试设备**: `172.12.2.108:37777 admin/admin1234`

> 真机 SDK 集成要点（2026-08-30 修复）记录已归档至 daily 2026-08-30.md，需要时再回查。
