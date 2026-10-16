# GraphFlow Runtime Upgrade

> Status: Spec-driven GraphRun foundation landed on 2026-08-24.

This document records the executable runtime layer added on top of the existing Loop Engineer / Selector-agents framework.

## Upgrade Scope

The upgrade follows `Loop-Engineer-Selector-to-GraphFlow-优化路线评审.md`:

- Keep existing Agent definitions and output schemas.
- Keep Artifact Store as the handoff mechanism.
- Keep Gate-Auto deterministic.
- Move orchestration from document-only rules toward an executable GraphFlow runtime.
- Keep Selector/Orchestrator-LLM as an assignment capability, not the owner of global flow control.
- Bind new GraphRuns to immutable Published SpecSnapshots, following `Spec驱动GraphFlow软件工厂架构优化计划.md`.
- Keep legacy task cards as a compatibility input and human-readable summary, not the long-term machine source of truth.

## Runtime Layout

```text
scheduler/
  main.py              # CLI entrypoint
  graph.py             # GraphFlow nodes and conditional edges
  state.py             # Runtime state, task-card model, transition constants
  task_card.py         # Markdown/frontmatter task-card loader
  legacy.py            # legacy task-card to ChangeSpec migration bridge
  artifacts.py         # Artifact path resolution and DONE artifact checks
  spec_flow.py         # ChangeSpec/ProductSpec validation, revision approval, and immutable Snapshot publishing
  specs.py             # Published SpecSnapshot loading, schema checks, content hash verification
  runs.py              # GraphRun spec binding, run-state storage, run validation
  plans.py             # ExecutionPlan generation and requirement/acceptance coverage validation
  drg.py               # DRG template compilation, node registry checks, DAG validation
  executor.py          # DRG dependency executor, RunState advancement, node event/artifact writing
  agent_invoker.py     # per-node Agent context pack generation for real handler integration
  gate_auto.py         # run-level gate-auto handler for executable acceptance checks
  conformance.py       # Requirement/Acceptance to evidence conformance reporting
  evidence.py          # evidence manifest hashing and run artifact index generation
  spec_change.py       # SpecChangeRequest generation and validation
  supersede.py         # vNext Snapshot to new GraphRun handoff and old-run supersede marking
  retry.py             # local invalidation, downstream propagation, retry preparation
  retry_policy.py      # retry limit policy, retry scheduling, escalation
  tool_contracts.py    # per-agent/node tool permissions and artifact boundary validation
  schema.py            # Task-card, Agent output, DONE guard validation
  transition.py        # transition guard validator
  gates.py             # deterministic expect evaluator and shell check runner
  events.py            # legacy task events and .loop/runs/{run_id}/events.jsonl writer/reader
  ui_design.py         # product-shape and UI approval helpers
  audit.py             # repository-wide runtime consistency audit
```

Runtime storage:

```text
.loop/events/{task_id}.jsonl                    # legacy compatibility
.loop/runs/{run_id}/spec-binding.json
.loop/runs/{run_id}/run-state.json
.loop/runs/{run_id}/execution-plan.json
.loop/runs/{run_id}/execution-graph.json
.loop/runs/{run_id}/tool-contract-report.json
.loop/runs/{run_id}/conformance-report.json
.loop/runs/{run_id}/evidence-manifest.json
.loop/runs/{run_id}/spec-change-request.json
.loop/runs/{run_id}/events.jsonl
.loop/runs/{run_id}/artifacts/
.loop/runs/{run_id}/context-packs/
.loop/runs/{run_id}/state-snapshots/
specs/{project}/drafts/{spec_id}-from-task-card-change-spec.json
specs/{project}/published/{spec_id}/v{N}/spec-snapshot.json
```

`shared/blackboard.md` remains the human-readable summary view. For new work, `.loop/runs/{run_id}/run-state.json` and `.loop/runs/{run_id}/events.jsonl` are the machine-readable execution source. `.loop/events/*.jsonl` remains available for legacy task-card events during migration.

## Handler Boundary

Handlers are node execution adapters. The DRG executor owns dependency order, RunState mutation, retries, event logging, and artifact registration. A handler owns the professional action for one node type or node id, such as running gate checks, test checks, review, deploy, or a future Agent invocation. This keeps graph control deterministic while allowing real execution capabilities to be added incrementally.

## Available Commands

```bash
python3 -m scheduler.main audit
python3 -m scheduler.main graph
python3 -m scheduler.main events task-001
python3 -m scheduler.main events run-001 --run
python3 -m scheduler.main validate-spec specs/web-app/published/task-001/v1/spec-snapshot.json
python3 -m scheduler.main validate-change-spec specs/web-app/drafts/task-001-change-spec.json
python3 -m scheduler.main legacy-task-to-change-spec .loop/active/web-app/task-001-demo.md
python3 -m scheduler.main publish-spec specs/web-app/drafts/task-001-change-spec.json --version 1
python3 -m scheduler.main smoke-flow specs/web-app/drafts/task-001-change-spec.json --version 1 --run-id run-001
python3 -m scheduler.main revise-spec-from-scr .loop/runs/run-001/spec-change-request.json specs/web-app/published/task-001/v1/spec-snapshot.json
python3 -m scheduler.main approve-revision-spec specs/web-app/drafts/task-001-v2-from-run-001-change-spec.json --approved-by spec-owner --reason "SCR reviewed"
python3 -m scheduler.main supersede-run run-001 specs/web-app/published/task-001/v2/spec-snapshot.json --new-run-id run-002
python3 -m scheduler.main create-run specs/web-app/published/task-001/v1/spec-snapshot.json --run-id run-001
python3 -m scheduler.main run-state run-001
python3 -m scheduler.main plan run-001
python3 -m scheduler.main compile-graph run-001
python3 -m scheduler.main tool-contract run-001
python3 -m scheduler.main evidence run-001
python3 -m scheduler.main execute run-001
python3 -m scheduler.main execute run-001 --max-steps 1
python3 -m scheduler.main execute run-001 --retry-policy .loop/retry-policy.json
python3 -m scheduler.main reset-node run-001 wp-r1_implement
python3 -m scheduler.main check-transition .loop/active/web-app/task-001-demo.md DONE
```

## Current Guarantees

- Validates ChangeSpec drafts before publishing:
  - required fields
  - object/list field shapes
  - no unresolved `open_questions`
  - at least one Requirement
  - at least one Acceptance Criterion
- Converts legacy task cards into ChangeSpec drafts:
  - reads frontmatter, title, goal, dependencies, project type, and structured Done items
  - maps Done items to executable Acceptance Criteria
  - records the source task card in `source_refs`
  - refuses conversion when no Done items exist
  - writes drafts under `specs/{project}/drafts/`
  - refuses to overwrite an existing converted draft
- Publishes immutable SpecSnapshots from ChangeSpecs:
  - builds `specs/{project}/published/{spec_id}/v{N}/spec-snapshot.json`
  - sets `status=PUBLISHED`
  - computes `content_hash`
  - refuses to overwrite an existing published version
  - can merge ProductSpec constraints into the published Snapshot
- Validates Published SpecSnapshots before GraphFlow binding:
  - required fields
  - supported schema version
  - `status=PUBLISHED`
  - positive integer version
  - content hash matching canonical snapshot content
- Creates GraphRuns with immutable spec binding:
  - `spec-binding.json`
  - `run-state.json`
  - `events.jsonl`
  - `state-snapshots/000-created.json`
- Validates a GraphRun keeps `spec-binding.json`, `run-state.json`, and the bound snapshot consistent.
- Generates deterministic `execution-plan.json` from the bound snapshot:
  - selects `web-feature-v1` or `qt-feature-v1`
  - creates WorkPackages from requirements
  - records Requirement and Acceptance coverage
  - validates coverage against the bound snapshot
- Compiles `execution-graph.json` from `execution-plan.json`:
  - uses registered node types only
  - keeps mandatory governance nodes present
  - verifies graph edges reference known nodes
  - rejects cycles so the DRG remains a DAG
  - verifies Requirement and Acceptance coverage reaches implementation/test nodes
  - stores a graph content hash
- Validates Tool Contracts for DRG nodes before execution:
  - maps each node agent to a registered ToolContract
  - checks each agent is allowed to execute the node type
  - checks each agent can write the node's required artifact type
  - renders run-local read/write boundaries such as `.loop/runs/{run_id}/artifacts/**`
  - writes `.loop/runs/{run_id}/tool-contract-report.json`
  - writes `tool_contract_validated`
  - blocks execution with `INVALID_TOOL_CONTRACT` when validation fails
- Prepares per-node Agent context packs:
  - writes `.loop/runs/{run_id}/context-packs/{node_id}.json`
  - includes the bound SpecRef, node payload, contract envelope, run inputs, and completed artifact refs
  - injects `context_pack_path` into node handlers
  - writes `agent_context_prepared`
- Executes `execution-graph.json` dependency order with a deterministic default handler:
  - starts only nodes whose dependencies are complete
  - writes `node_started`, `node_completed`, `artifact_written`, and `run_done` events
  - writes node result artifacts under `.loop/runs/{run_id}/artifacts/`
  - rejects handler success results that return missing, disallowed, unsafe, or non-existent artifacts
  - advances `run-state.json`
  - writes per-node state snapshots
  - supports partial execution with `--max-steps`
- Provides specialized deterministic handlers for core governance evidence:
  - final gate runs executable acceptance checks when `check`/`expect` are present
  - test nodes run scoped acceptance checks for their assigned Acceptance Criteria
  - gate nodes write structured gate results
  - review nodes write APPROVE review results
  - deploy nodes write local deterministic deploy evidence
  - conformance nodes write `conformance-report.json`
- Runs executable acceptance checks from Published SpecSnapshots:
  - reads `acceptance_criteria[].check`
  - evaluates `acceptance_criteria[].expect`
  - records stdout/stderr, exit code, and pass/fail per check
  - routes failed test/gate checks through RetryPolicy
- Generates `conformance-report.json`:
  - maps Requirements to implementation node evidence
  - maps Acceptance Criteria to test node evidence
  - reports `PASS` when all required evidence exists
  - reports `UNVERIFIED` when evidence is missing
- Generates `evidence-manifest.json`:
  - indexes run artifact refs and run-level files
  - records `sha256` and file size for each evidence file
  - includes `spec-binding.json`, `run-state.json`, `execution-plan.json`, `execution-graph.json`, `tool-contract-report.json`, and `conformance-report.json` when present
  - writes `evidence_manifest_written`
  - blocks final `DONE` if required evidence refs are missing
- Raises `spec-change-request.json` on conformance gaps:
  - moves RunState to `AWAITING_SPEC_CHANGE`
  - writes `spec_change_requested`
  - records affected requirements and acceptance criteria
  - keeps evidence refs tied to the conformance report
- Creates revision ChangeSpec drafts from SpecChangeRequests:
  - reads the SCR and its base SpecSnapshot
  - rejects SCR/base version mismatches
  - marks affected Requirements and Acceptance Criteria with `revision_requested`
  - writes a draft under `specs/{project}/drafts/`
  - requires revision approval before `publish-spec` can create a new immutable Snapshot
- Enforces revision approval before publishing revised specs:
  - initial ChangeSpecs can still publish normally
  - SCR-derived revision drafts require `approval.status=APPROVED`
  - rejected or missing approval blocks Snapshot publishing
  - approval metadata is carried into the Published SpecSnapshot for traceability
- Supersedes old GraphRuns when a newer SpecSnapshot is selected:
  - requires the new Snapshot to have the same `spec_id`
  - requires the new Snapshot version to be greater than the old bound version
  - creates a new GraphRun bound to the newer Snapshot
  - marks the old RunState as `SUPERSEDED`
  - keeps the old `spec-binding.json` unchanged
  - writes `run_superseded` on the old run and `run_supersedes` on the new run
- Supports local retry preparation with downstream invalidation:
  - computes the target node plus all downstream nodes from the DRG edges
  - removes those nodes from completed/failed/blocked/current state lists
  - increments retry count for the reset node
  - removes affected run-local node artifacts
  - removes run-level conformance/SCR artifacts when conformance is invalidated
  - writes `node_invalidated`
- Applies automatic retry policy for ordinary node failures:
  - writes `node_failed`
  - schedules a local reset when retry count is below the effective limit
  - writes `node_retry_scheduled`
  - moves the run back to `RUNNING`
  - writes `run_escalated` and moves to `ESCALATED` when retry limit is exhausted
- Supports fine-grained retry policy configuration:
  - default retry limit
  - per-node retry limits through `node_max_retries`
  - per-node-type retry limits through `node_type_max_retries`
  - failure-reason substring limits through `reason_max_retries`
  - allowed retryable node types through `retryable_node_types`
  - `execute --retry-policy FILE` loads the policy JSON for a run
- Supports a full local smoke flow from a ChangeSpec:
  - publishes an immutable SpecSnapshot
  - creates a GraphRun bound to that Snapshot
  - generates `execution-plan.json`
  - compiles `execution-graph.json`
  - validates Tool Contracts
  - executes deterministic handlers
  - writes `evidence-manifest.json`
- Exposes the new fixed governance skeleton:
  - Spec Binding
  - Planning
  - Plan Gate
  - Architecture Impact
  - DRG Compile
  - Implementation DRG
  - Final Gate
  - Conformance
  - Delivery Review
  - Deploy
- Loads task cards from `.loop/active/{project}/` and `.loop/done/{project}/`.
- Parses frontmatter and structured Done items.
- Validates known task states, phase, project type, and Done item shape.
- Checks DONE transition hard guards:
  - required artifacts by project type and phase
  - required Agent coverage
  - all Done items passed
  - ReviewAgent verdict
  - deployment success
  - environment precheck verdict
  - UI review approval when present
- Exposes GraphFlow business nodes and conditional edges separately from low-level task states.
- Models UI Design as an independent management loop:
  - product-shape confirmation
  - UI design contract generation
  - UI review approval
  - frontend/client implementation gate
- Supports JSONL event writing/reading.

Current run event types include `run_created`, `spec_bound`, `plan_generated`, `drg_compiled`, `tool_contract_validated`, `agent_context_prepared`, `node_started`, `node_completed`, `node_failed`, `artifact_written`, `evidence_manifest_written`, `spec_change_requested`, `node_invalidated`, `node_retry_scheduled`, `run_escalated`, `run_superseded`, `run_supersedes`, and `run_done`.

## Boundaries

This is not yet a full Agent executor. It does not call LLM Agents, mutate task cards, perform code edits, run real tests beyond configured acceptance shell checks, or deploy systems. The current executor proves dependency semantics, state mutation, event writing, artifact collection, conformance reporting, spec-change escalation, bounded retry policy, SCR-to-draft revision, revision approval gating, legacy task-card migration, vNext Snapshot handoff, and end-to-end smoke flow with deterministic handlers. The next upgrade should replace selected default handlers with real node handlers:

```text
spec_binding_node -> planning_node -> plan_gate_node -> architecture_impact_node
drg_compile_node -> implementation_drg_node -> final_gate_node
conformance_node -> delivery_review_node -> deploy_node -> done_node
```

The important shift is already in place: GraphFlow now has a code surface where snapshot binding, run state, event logs, artifact guards, and transition rules can be tested before any Agent is allowed to move work forward.

## Retry Policy JSON

```json
{
  "default_max_retries": 3,
  "node_max_retries": {
    "final_gate_node": 1
  },
  "node_type_max_retries": {
    "test": 2,
    "review": 0
  },
  "reason_max_retries": {
    "permission denied": 0
  },
  "retryable_node_types": [
    "implementation",
    "test",
    "gate",
    "review",
    "deploy"
  ]
}
```

Effective retry limit precedence:

```text
node_id -> failure reason substring -> node_type -> default_max_retries
```

## Tool Contract Model

Tool contracts are runtime permission envelopes for future real handlers. They are not Agent prompts and they do not replace sandboxing; they are the scheduler's machine-readable allowlist for what a node may attempt.

Each registered contract declares:

```json
{
  "agent": "test-agent",
  "node_types": ["test"],
  "allowed_tools": ["read_json", "shell_test", "write_json"],
  "read_paths": ["specs/**", ".loop/runs/{run_id}/**", "projects/**", "shared/**"],
  "write_paths": [".loop/runs/{run_id}/artifacts/**"],
  "artifact_types": ["test-result"]
}
```

The current deterministic executor validates these contracts before node execution and validates handler-returned artifacts before a node can complete. Later real handlers should consume the same contract envelope from their context pack to construct their execution sandbox.

## Migration Rule

The migration rule is "dual read, migration bridge, single write":

- Legacy task cards and `.loop/events/{task_id}.jsonl` remain readable.
- Legacy task cards can be converted to ChangeSpec drafts with `legacy-task-to-change-spec`.
- New machine state is written to `SpecSnapshot + GraphRun`.
- GraphFlow must not bind a draft spec.
- A GraphRun must not silently switch snapshots after creation.
- Spec mismatches move the run to `AWAITING_SPEC_CHANGE` and produce `spec-change-request.json`.
- Revised ChangeSpecs must be approved before publishing a new immutable Snapshot; adopting that Snapshot creates a new GraphRun and marks the old run `SUPERSEDED`.
