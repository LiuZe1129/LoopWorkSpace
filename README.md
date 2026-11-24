# LoopWorkSpace
based on loop-engineer software workspace

## Documentation

- [Harness Agent-Loop MVP 建设与评审基线](documentation/agent-loop-mvp-review-baseline.md)
- [SPEC-GraphFlow 当前缺陷与 MVP 差距评审](documentation/spec-graphflow-current-gap-review.md)
- [当前 SPEC-GraphFlow 与目标 Agent-Harness 架构对比](documentation/architecture-current-vs-agent-harness.md)
- [Agent-Harness MVP 详细修改计划](documentation/agent-harness-mvp-implementation-plan.md)
- [项目路径树与 Harness 文件定位](documentation/project-path-tree-harness-positioning.md)

## Agent-Harness MVP

Harness 正式入口固定使用版本化 AgentProfile、Model Gateway、Tool Policy 和 Docker
Sandbox；Docker 不可用时生成 `infrastructure` 失败结果，不回退宿主 Shell。
任务源码默认使用本地现有工程的启动时快照，不要求固定 Git commit；只有确定性验收通过且
源文件哈希未发生并发变化时，ChangeSet 才会原子回写本地工程。

```powershell
python -m harness.cli --help
python -m harness.cli task-state <execution_id>
python -m harness.cli task-timeline <execution_id>
python -m harness.cli task-metrics <execution_id>
python -m harness.cli evaluation-compare <baseline-report.json> <candidate-report.json>
```

生产环境建议使用不可变镜像摘要：

```powershell
$env:HARNESS_DOCKER_IMAGE = "registry.example/loop-harness@sha256:<digest>"
$env:HARNESS_REQUIRE_IMAGE_DIGEST = "true"
python -m scheduler.main execute <run_id>
```

本地发布门禁：

```powershell
python -B scripts/check_harness_comments.py
python -B scripts/check_harness_release_gate.py
python -B -m unittest discover -s tests -p "test_*.py"
```
