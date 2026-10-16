# Agent: Gate-Auto（自动闸门）

> L1 自动闸门执行器。**不是 LLM Agent**，而是确定性脚本执行器，负责执行任务卡 Done 项中声明的 check 命令。

---

## 1. 角色定位

L1 质量闸门，在 Agent 输出产物后、ReviewAgent 评审前执行。全部 pass 才进入 L2 评审；有 fail 项直接 RETRY，不进入 LLM 评审。

```
Agent 输出产物 → [Gate-Auto L1] → pass → AWAITING_REVIEW (L2)
                                  → fail → RETRY (回滚 + 重试)
```

---

## 2. 调用约定

| 项 | 值 |
|----|-----|
| trigger_state | `AWAITING_AUTO_GATE` |
| called_by | scheduler |
| parallel_capable | false |
| timeout_seconds | 300 |
| type | deterministic（非 LLM） |

---

## 3. 输入

从任务卡读取 Done 项：

```yaml
done_items:
  - id: d1
    check: "curl -s localhost:3000/api/health | jq .status"
    expect: "ok"
    status: pending
  - id: d2
    check: "cd projects/web-app/backend && npm test 2>&1 | tail -1"
    expect: "regex:\\d+ passing"
    status: pending
  - id: d3
    check: "cd projects/web-app/frontend && npm run lint"
    expect: "exitcode:0"
    status: pending
```

### C++/QT 客户端项目示例

```yaml
done_items:
  - id: d1
    check: "cd projects/pc-client && cmake -B build && cmake --build build --config Release"
    expect: "exitcode:0"
    status: pending
  - id: d2
    check: "cd projects/pc-client/build && ctest --output-on-failure"
    expect: "regex:100% tests passed"
    status: pending
  - id: d3
    check: "cd projects/pc-client && git diff --check"
    expect: "exitcode:0"
    status: pending
```

---

## 4. 输出 Schema

```json
{
  "agent": "gate-auto",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {"type": "report", "path": ".loop/artifacts/{project}/task-xxx/round-1/gate-result.json", "description": "闸门结果"}
  ],
  "gate_result": {
    "verdict": "PASS",
    "total_checks": 3,
    "passed": 3,
    "failed": 0,
    "results": [
      {
        "id": "d1",
        "check": "curl -s localhost:3000/api/health | jq .status",
        "expect": "ok",
        "actual": "ok",
        "passed": true
      },
      {
        "id": "d2",
        "check": "cd projects/web-app/backend && npm test 2>&1 | tail -1",
        "expect": "regex:\\d+ passing",
        "actual": "8 passing",
        "passed": true
      },
      {
        "id": "d3",
        "check": "cd projects/web-app/frontend && npm run lint",
        "expect": "exitcode:0",
        "actual": "",
        "passed": true
      }
    ]
  },
  "next_action": "AWAITING_REVIEW",
  "summary": "L1 自动闸门 3/3 通过"
}
```

---

## 5. expect 匹配模式

| 模式 | 格式 | 示例 | 判定 |
|------|------|------|------|
| 精确匹配 | `expect: "ok"` | stdout = "ok" | 完全相等 |
| 正则匹配 | `expect: "regex:\\d+ passing"` | stdout 匹配正则 | `re.match` |
| 包含匹配 | `expect: "contains:passed"` | stdout 包含 "passed" | `in` 判定 |
| 数值比较 | `expect: ">= 80"` | stdout 数值 ≥ 80 | 数值比较 |
| 退出码 | `expect: "exitcode:0"` | 命令退出码 = 0 | `$?` 判定 |
| 非零退出码 | `expect: "exitcode:nonzero"` | 命令退出码 ≠ 0 | 预期失败 |

---

## 6. 执行逻辑（伪代码）

> **C-2 修复**：`run_auto_gate` 按 `gate_stage` 筛选当前 Agent 可执行的 Done 项，避免中间 Agent 阶段跑全链路 check 失败导致 RETRY 死循环。
> 详见 `agents/orchestrator.md` §5.2。
>
> **S-5 修复**：spec-agent / orchestrator-llm 的 L1 闸门走 `schema_check` 模式（非 shell_exec），校验 JSON 结构完整性。其他 Agent 走 `shell_exec` 模式。

```
# S-5 修复：schema_check 模式——校验 JSON 产物结构完整性（无 shell 执行）
SCHEMA_CHECK_MAP = {
    "spec-agent": {
        "artifact": "spec.json",
        "required_fields": ["agent", "task_id", "round", "status", "artifacts", "task_card"],
        "nested": {
            "task_card": ["id", "goal", "done_items", "constraints"],
            "task_card.done_items[]": ["id", "check", "expect", "status"]
        }
    },
    "orchestrator-llm": {
        "artifact": "assignment.json",
        "required_fields": ["agent", "task_id", "round", "status", "artifacts", "assignment"],
        "nested": {
            "assignment": ["project_type", "subtasks", "execution_order"],
            "assignment.subtasks[]": ["id", "name", "assigned_to", "dependencies"]
        }
    }
}

function run_schema_check(task_card, current_agent):
    """S-5 修复：schema_check 模式——校验 JSON 产物字段完整性"""
    config = SCHEMA_CHECK_MAP[current_agent]
    artifact_path = f".loop/artifacts/{task_card.project}/{task_card.id}/round-{task_card.round}/{config.artifact}"

    results = []
    # 校验文件存在
    if not file_exists(artifact_path):
        results.append({
            id: "schema_file_exists",
            check: f"file_exists({config.artifact})",
            expect: "true",
            actual: "false",
            passed: false,
            stage: current_agent
        })
    else:
        data = read_json(artifact_path)
        # 校验顶层字段
        for field in config.required_fields:
            passed = field in data and data[field] is not None
            results.append({
                id: f"schema_{field}",
                check: f"{config.artifact}.{field} exists",
                expect: "non-null",
                actual: str(data.get(field)),
                passed: passed,
                stage: current_agent
            })
        # 校验嵌套字段
        for nested_path, fields in config.nested.items():
            if "[]" in nested_path:
                # 数组嵌套（如 task_card.done_items[]）
                parts = nested_path.replace("[]", "").split(".")
                arr = data
                for p in parts:
                    arr = arr.get(p, []) if isinstance(arr, dict) else []
                for i, item in enumerate(arr):
                    for f in fields:
                        passed = f in item and item[f] is not None
                        results.append({
                            id: f"schema_{nested_path}_{f}[{i}]",
                            check: f"{config.artifact}.{nested_path}.{f}[{i}]",
                            expect: "non-null",
                            actual: str(item.get(f)),
                            passed: passed,
                            stage: current_agent
                        })
            else:
                # 对象嵌套（如 task_card）
                obj = data.get(nested_path, {})
                for f in fields:
                    passed = f in obj and obj[f] is not None
                    results.append({
                        id: f"schema_{nested_path}_{f}",
                        check: f"{config.artifact}.{nested_path}.{f}",
                        expect: "non-null",
                        actual: str(obj.get(f)),
                        passed: passed,
                        stage: current_agent
                    })

    # 更新 Done 项 status（schema_check 对应的 Done 项标记 passed/failed）
    for r in results:
        if r.passed:
            task_card.update_done_status(r.id, "passed")
        else:
            task_card.update_done_status(r.id, "failed")

    return results

function run_auto_gate(task_card, current_agent=None):
    # S-5 修复：spec-agent / orchestrator-llm 走 schema_check 模式
    if current_agent in SCHEMA_CHECK_MAP:
        results = run_schema_check(task_card, current_agent)
        passed_count = count(results, passed=true)
        failed_count = count(results, passed=false)
        verdict = "PASS" if failed_count == 0 else "FAIL"
        artifact = {
            agent: "gate-auto",
            mode: "schema_check",
            task_id: task_card.id,
            round: task_card.round,
            gate_result: {
                verdict: verdict,
                total_checks: len(results),
                passed: passed_count,
                failed: failed_count,
                current_agent: current_agent,
                results: results
            },
            next_action: "AWAITING_REVIEW" if verdict == "PASS" else "RETRY",
            summary: f"L1 Schema 闸门 {passed_count}/{len(results)} 通过（agent={current_agent}）"
        }
        save_artifact(artifact, f".loop/artifacts/{task_card.project}/{task_card.id}/round-{task_card.round}/gate-result.json")
        return artifact

    # 常规模式：shell_exec 执行 Done 项 check 命令
    done_items = parse_done_items(task_card)
    results = []

    for item in done_items:
        # C-2 修复：按 gate_stage 筛选
        stage = item.get("gate_stage", "final")
        if current_agent is not None:
            # 中间 Agent 阶段：只执行该 Agent 的项 + any 项
            if stage == "final":
                continue
            if stage != "any" and stage != current_agent:
                continue
        else:
            # 最终阶段：只执行 final 项
            if stage != "final":
                continue

        output, exit_code = shell_exec(item.check, timeout=60)
        passed = evaluate(output, exit_code, item.expect)

        results.append({
            id: item.id,
            check: item.check,
            expect: item.expect,
            actual: output.strip(),
            passed: passed,
            stage: stage
        })

        # 更新任务卡 Done 项 status
        task_card.update_done_status(item.id, "passed" if passed else "failed")

    passed_count = count(results, passed=true)
    failed_count = count(results, passed=false)

    verdict = "PASS" if failed_count == 0 else "FAIL"

    # 写入 artifact
    artifact = {
        agent: "gate-auto",
        mode: "shell_exec",
        task_id: task_card.id,
        round: task_card.round,
        gate_result: {
            verdict: verdict,
            total_checks: len(results),
            passed: passed_count,
            failed: failed_count,
            current_agent: current_agent,  # C-2 修复：记录本次 gate 针对的 Agent
            results: results
        },
        next_action: "AWAITING_REVIEW" if verdict == "PASS" else "RETRY",
        summary: f"L1 自动闸门 {passed_count}/{len(results)} 通过（agent={current_agent or 'final'}）"
    }
    save_artifact(artifact, f".loop/artifacts/{task_card.project}/{task_card.id}/round-{task_card.round}/gate-result.json")

    return artifact
```

---

## 7. 失败处理

| 场景 | 处理 |
|------|------|
| check 命令执行超时（60s） | 该项标记 `passed: false`，actual 记录 "TIMEOUT" |
| check 命令不存在 | 该项标记 `passed: false`，actual 记录 "COMMAND_NOT_FOUND" |
| 闸门整体超时（300s） | 整体 verdict = "FAIL"，触发 FAILED |
| Done 项无 check 字段 | 跳过该项，日志记录 "NO_CHECK_DEFINED" |

闸门失败（有 fail 项）→ 调度器执行 `transition_to(RETRY)`，回滚 checkpoint。

---

## 8. 能力声明

- tools: [shell_exec, schema_check]
- capabilities: [declarative_check_execution, result_evaluation, json_schema_validation]
- model: 无（确定性脚本，非 LLM）
- temperature: 无

---

## 9. 行为约束

- 不调用 LLM
- 不修改业务代码
- 只执行任务卡 Done 项声明的 check 命令
- 结果必须落盘到 artifact store
- 每项 check 有独立超时（60s），整体超时 300s
- **shell_exec 跨平台**：Windows 上使用 Git Bash 执行 check 命令（支持 `tail`/`grep`/`2>&1|`/`test -f` 等 bash 语法）；Linux/Mac 使用原生 bash。调度器在调用 shell_exec 前根据 `.loop/context.md` 的 OS 字段选择 shell

## 10. 职责边界（与 TestAgent 严格区分）

Gate-Auto 和 TestAgent 职责不同，**不可互相替代，不可合并**：

| 维度 | Gate-Auto（L1 闸门） | TestAgent |
|------|---------------------|-----------|
| 执行者 | 确定性脚本（无 LLM） | LLM Agent |
| 职责 | 执行任务卡 Done 项声明的 check 命令 | 编写测试 + 执行测试套件 + 分析覆盖率 |
| 产物 | gate-result.json | test-report.json |
| 执行时机 | TestAgent 之后（test-report.json 存在后） | 所有 DevAgent 之后（所有 diff 齐全后） |
| check 类型 | 声明式环境验证（服务启动/端点响应/构建成功） | 行为验证（功能正确/边界条件/异常路径） |
| 失败动作 | RETRY（回滚 checkpoint） | status: partial，建议 RETRY DevAgent |

### Done 项 check 命令设计原则

为避免 Gate-Auto 和 TestAgent 重复执行，任务卡 Done 项的 check 命令应遵循：

- **Gate-Auto 的 check**：偏向**声明式环境验证**
  - 示例：`curl -s localhost:8000/health`、`npm run build`、`cmake --build build --config Release`
  - expect：`exitcode:0`、`contains:ok`、`contains:built`
- **TestAgent 的测试**：偏向**行为验证**
  - 示例：`pytest tests/test_auth.py`、`npm test -- --run tests/ProgressBar.test.tsx`
  - TestAgent 产出 test-report.json，含覆盖率分析

### 重复执行的处理

如果 Done 项 check 和 TestAgent 测试重叠（如 Done 项 d7 是 `pytest tests/test_auth.py`）：
- Gate-Auto 仍然执行该 check（声明式，不可跳过）
- TestAgent 的 test-report.json 作为 ReviewAgent 的**补充输入**
- 两者产物独立，不可合并

---

## 11. 环境预检模式（ENV_CHECK）

> 本模式是 Gate-Auto 的第二个执行入口，用于在架构 APPROVED 后、开发 DISPATCHED 前预检本机开发环境，避免开发阶段才发现环境不匹配（如 Python 低版本导致 cmd 错误）。

### 11.1 触发条件

| 项 | 值 |
|----|-----|
| trigger_state | `ENV_CHECK` |
| called_by | scheduler |
| 触发时机 | 架构阶段 `AWAITING_REVIEW` → `APPROVED` 之后，开发阶段 `DISPATCHED` 之前 |
| 仅首次触发 | 只在架构阶段首次 APPROVED 后执行一次；后续子任务 APPROVED 不再触发 |
| parallel_capable | false |
| timeout_seconds | 120 |
| type | deterministic（非 LLM） |

### 11.2 输入

从 `architecture.json` 读取 `environment_requirements` 字段：

```json
"environment_requirements": [
  {"tool": "python", "min_version": "3.10", "required": true, "check_cmd": "py --version", "description": "后端运行时（Windows 用 py，Linux/Mac 用 python3）"},
  {"tool": "node", "min_version": "18.0", "required": true, "check_cmd": "node --version", "description": "前端构建"},
  {"tool": "postgresql", "min_version": "14", "required": false, "check_cmd": "psql --version", "description": "可选，Docker 替代"}
]
```

### 11.3 输出 Schema（env-check-result.json）

```json
{
  "agent": "gate-auto",
  "mode": "env_precheck",
  "task_id": "task-xxx",
  "round": 1,
  "status": "success",
  "artifacts": [
    {"type": "report", "path": ".loop/artifacts/{project}/task-xxx/round-1/env-check-result.json", "description": "环境预检结果"}
  ],
  "env_check_result": {
    "verdict": "PASS",
    "total_tools": 3,
    "passed": 2,
    "warned": 1,
    "failed": 0,
    "results": [
      {
        "tool": "python",
        "required": true,
        "check_cmd": "python --version",
        "min_version": "3.10",
        "actual_version": "3.12.7",
        "status": "pass",
        "message": "满足最低版本要求"
      },
      {
        "tool": "node",
        "required": true,
        "check_cmd": "node --version",
        "min_version": "18.0",
        "actual_version": "20.10.0",
        "status": "pass",
        "message": "满足最低版本要求"
      },
      {
        "tool": "postgresql",
        "required": false,
        "check_cmd": "psql --version",
        "min_version": "14",
        "actual_version": null,
        "status": "warn",
        "message": "未安装（可选，可用 Docker 启动）"
      }
    ],
    "upgrade_suggestions": []
  },
  "next_action": "DISPATCHED",
  "summary": "环境预检 2 pass / 1 warn / 0 fail"
}
```

### 11.4 版本比对逻辑

```python
def compare_version(actual: str, min_required: str) -> bool:
    """语义化版本比对：actual >= min_required 返回 True"""
    actual_parts = [int(x) for x in re.findall(r'\d+', actual)]
    min_parts = [int(x) for x in min_required.split('.')]
    # 补齐位数
    while len(actual_parts) < len(min_parts):
        actual_parts.append(0)
    while len(min_parts) < len(actual_parts):
        min_parts.append(0)
    return actual_parts >= min_parts

def run_env_precheck(architecture_json):
    requirements = architecture_json["architecture"]["environment_requirements"]
    results = []
    failed_required = 0
    warned_optional = 0

    for req in requirements:
        output, exit_code = shell_exec(req["check_cmd"], timeout=15)

        if exit_code != 0:
            # 命令执行失败 = 工具未安装或不在 PATH
            if req["required"]:
                status = "fail"
                failed_required += 1
            else:
                status = "warn"
                warned_optional += 1
            actual_version = None
            message = f"未安装或不在 PATH（{'必装，FAIL' if req['required'] else '可选，WARN'}）"
        else:
            actual_version = parse_version_from_output(output)
            if actual_version is None:
                status = "fail" if req["required"] else "warn"
                message = f"无法解析版本号：{output.strip()}"
                if req["required"]:
                    failed_required += 1
                else:
                    warned_optional += 1
            elif compare_version(actual_version, req["min_version"]):
                status = "pass"
                message = "满足最低版本要求"
            else:
                status = "fail" if req["required"] else "warn"
                message = f"版本 {actual_version} 低于最低要求 {req['min_version']}"
                if req["required"]:
                    failed_required += 1
                else:
                    warned_optional += 1

        results.append({
            "tool": req["tool"],
            "required": req["required"],
            "check_cmd": req["check_cmd"],
            "min_version": req["min_version"],
            "actual_version": actual_version,
            "status": status,
            "message": message
        })

    # verdict 判定：必装项任一 fail → FAIL；可选项 fail 仅 WARN
    verdict = "FAIL" if failed_required > 0 else "PASS"

    upgrade_suggestions = []
    for r in results:
        if r["status"] == "fail":
            upgrade_suggestions.append({
                "tool": r["tool"],
                "current": r["actual_version"],
                "required": r["min_version"],
                "suggestion": get_upgrade_cmd(r["tool"])  # 如 py -3.12 / nvm install 18 / docker run postgres
            })

    return {
        "verdict": verdict,
        "total_tools": len(results),
        "passed": sum(1 for r in results if r["status"] == "pass"),
        "warned": warned_optional,
        "failed": failed_required,
        "results": results,
        "upgrade_suggestions": upgrade_suggestions
    }
```

### 11.5 失败处理

| 场景 | verdict | next_action | 状态转换 |
|------|---------|-------------|---------|
| 全部 pass（必装+可选都满足） | PASS | DISPATCHED | ENV_CHECK → DISPATCHED |
| 必装全 pass + 可选有 warn | PASS | DISPATCHED | ENV_CHECK → DISPATCHED |
| 必装有 fail | FAIL | AWAITING_HUMAN | ENV_CHECK → AWAITING_HUMAN |
| check_cmd 超时（15s） | 该项 fail/warn | 按上述规则 | 同上 |

### 11.6 与 §4 常规闸门模式的区别

| 维度 | §4 常规闸门（AWAITING_AUTO_GATE） | §11 环境预检（ENV_CHECK） |
|------|-------------------------------|------------------------|
| 触发时机 | 每个 Agent 输出产物后 | 仅架构阶段 APPROVED 后一次 |
| 输入来源 | 任务卡 done_items 的 check 命令 | architecture.json 的 environment_requirements |
| 检查对象 | 代码产物（服务/端点/构建） | 开发工具链（python/node/cmake/...） |
| 失败动作 | RETRY（回滚 checkpoint 重跑 Agent） | AWAITING_HUMAN（等用户升级环境，不重试） |
| 产物 | gate-result.json | env-check-result.json |
| LLM 参与 | 无 | 无 |

**关键差异：环境不匹配不属于代码或 LLM 决策问题，RETRY 重试结果不变**。因此失败必须走 AWAITING_HUMAN 分支，由用户升级环境后人工 APPROVE 重新预检，超时 24h 或 REJECT 则升级到 ESCALATED。

---

## 12. 经验沉淀模式（Experience Sedimentation）

> 本模式是 Gate-Auto 的第三个执行入口，用于在任务 DONE 后、归档前执行经验沉淀。复用 Gate-Auto 的事实验证能力（shell_exec），非新增 Agent。

### 12.1 触发条件

| 项 | 值 |
|----|-----|
| trigger | `post-DONE hook`（调度器在 `archive_or_escalate()` 前调用） |
| called_by | scheduler（`sediment_experience()` 函数） |
| 触发时机 | 任务状态 = DONE，归档前 |
| parallel_capable | false |
| timeout_seconds | 120 |
| type | deterministic + LLM 辅助（事实验证为确定性，质量评估委托 ReviewAgent） |

### 12.2 输入

从调度器运行时上下文读取：

```json
{
  "task_card": "当前任务卡",
  "executed_agents": ["spec-agent", "architect-agent", ...],
  "artifacts_dir": ".loop/artifacts/{project}/{task_id}/round-{N}/",
  "pending_experience_updates": [
    {
      "agent": "backend-dev",
      "task_id": "task-010",
      "round": 1,
      "experience_update": {
        "failure_memory_candidates": [...],
        "working_notes_update": {...},
        "recipe_candidate": null,
        "decision_candidate": null
      }
    }
  ]
}
```

### 12.3 三级门禁执行逻辑

> **C-5 修复**：架构阶段（phase=architecture）无 deploy-result.json，Level 3 跳过 deploy 校验。
> **C-6 修复**：FAILED/ESCALATED 终态也执行经验沉淀（仅 Level 1+2，跳过 Level 3），失败经验更有价值。
> **H-6 修复**：Working Notes 写入需经过 Level 2 门禁验证（代码证据存在 + L1 PASS），不再无条件写入。
> **M-6 修复**：记忆写入使用文件锁（`flock`/`msvcrt.locking`），防止多项目并行写入冲突。
> **M-7 修复**：产出 `sediment-result.json`，记录经验沉淀决策过程（哪些候选被丢弃/写入/升级）。

```
function sediment_experience(runtime_ctx):
    updates = runtime_ctx.pending_experience_updates
    if not updates:
        return  # 无经验更新，跳过

    task_card = runtime_ctx.task_card
    phase = task_card.phase
    artifacts_dir = f".loop/artifacts/{task_card.project}/{task_card.id}/round-{task_card.round}/"
    arch_dir = f".loop/artifacts/{task_card.project}/{task_card.id}/round-{task_card.arch_round}/"

    # 可观测性产物（M-7 修复）
    sediment_log = {
        task_id: task_card.id,
        phase: phase,
        final_status: runtime_ctx.status,
        total_candidates: len(updates),
        level1_stored: 0,
        level2_verified: [],
        level2_dropped: [],
        level3_written: [],
        working_notes_written: [],
        working_notes_dropped: [],
        recipes_created: [],
        errors: []
    }

    # --- Level 1: Session Summary（直接存，已有机制，无变化） ---
    # 各 Agent 的输出 JSON 已存入 artifact store，无需额外处理
    sediment_log.level1_stored = len(updates)

    # --- Level 2: 事实验证（Gate-Auto 执行） ---
    # C-3 修复：每个 update 携带自己的 round（Agent 实际执行的轮次），
    #          Level 2 验证必须按 update.round 读取产物，不能用最终轮 artifacts_dir
    #          多子任务/多轮 RETRY 场景下，经验来自 round-2 但验证读 round-5 会出错
    verified_candidates = []
    for update in updates:
        # C-3 修复：按 update.round 计算该轮的 artifacts 目录
        update_dir = f".loop/artifacts/{task_card.project}/{task_card.id}/round-{update.round}/"

        for candidate in update.experience_update.failure_memory_candidates:
            # 验证1：代码变更文件是否存在（用 update.round 读取 Agent 产物）
            agent_output = read_artifact(update.agent, update.task_id, update.round)
            code_files_exist = check_files_in_changes(agent_output.changes, candidate)
            if not code_files_exist:
                sediment_log.level2_dropped.append({candidate, reason: "代码证据不存在"})
                continue

            # C-3 修复：验证2 - gate-result.json 必须存在（用 update.round 读取，非最终轮）
            gate_result = read_json(f"{update_dir}/gate-result.json")
            if gate_result is None:
                sediment_log.level2_dropped.append({candidate, reason: "gate-result.json 不存在（update.round）"})
                continue

            # C-3 修复：gate verdict 校验逻辑按 update.round 与最终轮的关系区分：
            # - DONE 终态 + update.round == 最终轮：gate 必须 PASS（最终轮成功了）
            # - DONE 终态 + update.round < 最终轮（RETRY 轮经验）：gate 可能 FAIL 是正常的，
            #   失败经验正是在 RETRY 轮中产生的，不应丢弃——代码证据存在即可通过 Level 2
            # - FAILED/ESCALATED 终态：gate 可能 FAIL，失败经验有价值，直接通过 Level 2
            if runtime_ctx.status == DONE and update.round == task_card.round:
                # 最终轮的失败记忆候选：gate 必须 PASS
                if gate_result.gate_result.verdict != "PASS":
                    sediment_log.level2_dropped.append({candidate, reason: "最终轮 L1 闸门未通过"})
                    continue
            # 否则（RETRY 轮经验 or 失败终态）：gate FAIL 不阻塞，候选通过 Level 2

            verified_candidates.append({
                ...candidate,
                agent: update.agent,
                task_id: update.task_id,
                round: update.round,  # C-3 修复：记录验证时的 round
                verified_level: 2
            })
            sediment_log.level2_verified.append(candidate)

    # --- Level 3: Canonical Memory 写入（仅 DONE 时执行） ---
    # C-5 修复：架构阶段无 deploy-result.json，仅校验 review.json
    # C-6 修复：FAILED/ESCALATED 时跳过 Level 3，直接写入 Level 2 验证通过的失败记忆
    # S-4 修复：最终轮 review.json 是最后一个 Agent（开发阶段=devops-agent）的评审结果。
    #   前序 Agent 的 review APPROVE 由状态机保证——任一 Agent 的 review REJECT 会进入
    #   REJECTED→RETRY/ESCALATED，不会到达 DONE。故读最终轮 review.json 确认 APPROVE
    #   即可代表整个任务链路通过。deploy-result.json 同理（devops-agent 是最后一个 Agent）。
    if runtime_ctx.status == DONE:
        # Level 3 校验用最终轮的 review.json + deploy-result.json（任务级整体校验，与单个 update 无关）
        review = read_json(f"{artifacts_dir}/review.json")
        review_ok = (review is not None and review.verdict == "APPROVE")

        # 架构阶段无 deploy-result.json，跳过 deploy 校验
        deploy_ok = True
        if phase != "architecture":
            deploy = read_json(f"{artifacts_dir}/deploy-result.json")
            deploy_ok = (deploy is not None and deploy.deploy.success == true)

        if review_ok and deploy_ok:
            with file_lock(".loop/memory/.lock"):  # M-6 修复：文件锁
                for candidate in verified_candidates:
                    # 写入失败记忆
                    fm_id = generate_fm_id()
                    write_failure_memory(fm_id, candidate)
                    sediment_log.level3_written.append(fm_id)

                    # 频率检测：检查同类失败是否达到升级阈值
                    similar_fms = search_failure_memory_by_tags(candidate.applies_to)
                    if len(similar_fms) >= 2:
                        # 同类失败第 3 次 → 升级为 Recipe
                        recipe_id = create_recipe_from_failures([candidate] + similar_fms)
                        sediment_log.recipes_created.append(recipe_id)
                    elif len(similar_fms) == 1:
                        # 同类失败第 2 次 → 升级 confidence
                        update_failure_memory_confidence(candidate, "high")
                        update_failure_memory_confidence(similar_fms[0], "high")
    elif runtime_ctx.status in [FAILED, ESCALATED]:
        # C-6 修复：失败任务的经验更宝贵，Level 2 验证通过即可写入
        with file_lock(".loop/memory/.lock"):
            for candidate in verified_candidates:
                fm_id = generate_fm_id()
                write_failure_memory(fm_id, candidate, confidence_boost=True)  # 失败经验标记高置信
                sediment_log.level3_written.append({fm_id, note: "from_failed_task"})

    # --- 更新产物索引 ---
    with file_lock(".loop/memory/.lock"):
        update_artifact_index(runtime_ctx.task_card, updates)

    # --- 更新 Agent 岗位笔记（H-6 修复：需经 Level 2 门禁验证） ---
    # C-3 修复：Working Notes 验证也按 update.round 读取产物
    for update in updates:
        if update.experience_update.working_notes_update:
            # H-6 修复：Working Notes 也需 Level 2 验证
            update_dir = f".loop/artifacts/{task_card.project}/{task_card.id}/round-{update.round}/"
            agent_output = read_artifact(update.agent, update.task_id, update.round)
            if agent_output is None:
                sediment_log.working_notes_dropped.append({update.agent, reason: "Agent 产物不存在"})
                continue

            # FAILED/ESCALATED 时放宽：允许写入失败过程中的注意事项
            if runtime_ctx.status in [FAILED, ESCALATED]:
                with file_lock(".loop/memory/.lock"):
                    update_agent_working_notes(update.agent, update.experience_update.working_notes_update)
                sediment_log.working_notes_written.append(update.agent)
            else:
                # DONE 时需 gate-result PASS（C-3 修复：按 update.round 读取）
                # 如果 update.round < 最终轮（RETRY 轮经验），gate 可能 FAIL，
                # 但 Working Notes 仍可写入（这是 RETRY 过程中发现的注意事项）
                gate_result = read_json(f"{update_dir}/gate-result.json")
                if gate_result is None:
                    sediment_log.working_notes_dropped.append({update.agent, reason: "gate-result.json 不存在"})
                    continue
                # 最终轮 update：gate 必须 PASS
                # RETRY 轮 update：gate 可能 FAIL，仍允许写入（失败过程的注意事项有价值）
                if update.round == task_card.round and gate_result.gate_result.verdict != "PASS":
                    sediment_log.working_notes_dropped.append({update.agent, reason: "最终轮 L1 闸门未通过"})
                    continue
                with file_lock(".loop/memory/.lock"):
                    update_agent_working_notes(update.agent, update.experience_update.working_notes_update)
                sediment_log.working_notes_written.append(update.agent)

    # --- 决策候选处理 ---
    for update in updates:
        if update.experience_update.decision_candidate:
            # 决策候选需要 L3 人工确认（架构变更必触 L3）
            flag_for_l3_review(update.experience_update.decision_candidate)

    # --- M-7 修复：产出 sediment-result.json ---
    sediment_result_path = f"{artifacts_dir}/sediment-result.json"
    write_json(sediment_result_path, sediment_log)
```

### 12.4 经验沉淀决策矩阵

| 经验类型 | Level 1 | Level 2（Gate-Auto） | Level 3（调度器） | 写入目标 |
|---------|---------|---------------------|------------------|---------|
| 失败经验 | Agent 自报告 | 代码证据存在 + L1 PASS | review APPROVE + deploy success（DONE）；FAILED/ESCALATED 时仅 Level 2 即可写入 | `failure-memory/FM-NNN.md` |
| 模式经验 | Agent 自报告 | 代码证据存在 + L1 PASS（H-6 修复） | ReviewAgent 认可（review.json verdict=APPROVE） | `agents/{name}/memory.md` |
| 注意事项 | Agent 自报告 | Agent 产物存在（H-6 修复）；FAILED/ESCALATED 时放宽为产物存在即可 | ReviewAgent 认可；FAILED/ESCALATED 时跳过 | `agents/{name}/memory.md` |
| Recipe 候选 | 调度器检测 | 2+ 任务成功 | 第 3 次成功 | `recipes/xxx.md` |
| 决策记录 | Agent 自报告 | — | L3 人工确认 | `decision-log.md` |
| 产物索引 | Agent 输出 | — | 无条件 | `artifact-index.md` |

> **H-5 修复**：Level 3 门禁在 FAILED/ESCALATED 终态时放宽。失败任务的经验（特别是 RETRY 过程中发现的根因）往往比成功任务更有价值。短会话模式下，前几轮 RETRY 的上下文已销毁，Agent 在最终轮成功后可能无法完整回忆失败原因——因此 FAILED/ESCALATED 时直接写入 Level 2 验证通过的失败记忆，不要求 review APPROVE + deploy success。
> **H-6 修复**：Working Notes 写入不再无条件，需经 Level 2 门禁验证（Agent 产物存在 + L1 PASS，或终态时产物存在即可）。
> **C-3 修复**：Level 2 验证和 Working Notes 验证都按 `update.round`（Agent 实际执行轮次）读取产物，而非最终轮 `task_card.round`。多轮 RETRY 场景下，经验来自 round-2 但验证读 round-5 会错位。RETRY 轮的失败经验：gate FAIL 是正常的，不作为丢弃依据（这正是失败经验的来源）。

### 12.5 不沉淀的情况（反面清单）

| 情况 | 原因 | 处理 |
|------|------|------|
| Agent 自述但无代码证据 | 不可验证 | 丢弃 |
| 任务特定业务逻辑 | 不可复用 | 仅存 Session Summary |
| 偶发性环境问题 | 不会重复 | 仅存 Session Summary |
| 被 ReviewAgent REJECT 的"模式" | 未被认可 | 丢弃 |
| 修复方案未通过 L1 验证 | 未验证的修复 | 等修复通过后再沉淀 |
| 单个任务的成功流程 | 不足以成为 Recipe | 记为 working-notes |

### 12.6 与 §4/§11 的区别

| 维度 | §4 常规闸门 | §11 环境预检 | §12 经验沉淀 |
|------|-----------|------------|------------|
| 触发时机 | Agent 输出产物后 | 架构 APPROVED 后一次 | 任务 DONE 后归档前 |
| 输入来源 | 任务卡 done_items | architecture.json | 各 Agent 的 experience_update |
| 检查对象 | 代码产物 | 开发工具链 | 经验候选的真实性/可复用性 |
| 失败动作 | RETRY | AWAITING_HUMAN | 丢弃候选（不阻塞归档） |
| 产物 | gate-result.json | env-check-result.json | failure-memory/ + recipes/ + memory.md 更新 |
| LLM 参与 | 无 | 无 | 事实验证无 LLM；质量评估委托 ReviewAgent |
