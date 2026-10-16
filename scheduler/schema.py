from __future__ import annotations

from typing import Any

from .artifacts import ArtifactManager
from .state import (
    ARCHITECTURE_REQUIRED_AGENTS,
    DEVELOPMENT_REQUIRED_BASE,
    DYNAMIC_PRODUCT_MAP,
    LEGAL_TRANSITIONS,
    TaskCard,
    ValidationResult,
)


BASIC_AGENT_FIELDS = ["agent", "task_id", "round", "status", "artifacts", "summary"]

AGENT_REQUIRED_FIELDS: dict[str, list[str]] = {
    "spec-agent": BASIC_AGENT_FIELDS + ["task_card"],
    "orchestrator-llm": BASIC_AGENT_FIELDS + ["assignment"],
    "architect-agent": BASIC_AGENT_FIELDS + ["architecture"],
    "ui-design-agent": BASIC_AGENT_FIELDS + ["ui_design"],
    "test-agent": BASIC_AGENT_FIELDS + ["test_result"],
    "gate-auto": BASIC_AGENT_FIELDS + ["gate_result"],
    "review-agent": BASIC_AGENT_FIELDS + ["verdict", "issues"],
    "devops-agent": BASIC_AGENT_FIELDS + ["build", "deploy"],
}

UI_REVIEW_APPROVED_VALUES = {"APPROVE", "APPROVED"}


def validate_task_card(task: TaskCard) -> ValidationResult:
    result = ValidationResult(ok=True)
    required = ["id", "project", "status", "phase", "project_type", "round", "retry_count"]
    for field in required:
        if task.raw_frontmatter.get(field) in (None, ""):
            result.add("task_card.missing_field", "error", f"missing frontmatter field: {field}", str(task.path))
    if task.status not in LEGAL_TRANSITIONS:
        result.add("task_card.invalid_status", "error", f"unknown status: {task.status}", str(task.path))
    if task.phase not in {"architecture", "development"}:
        result.add("task_card.invalid_phase", "error", f"unknown phase: {task.phase}", str(task.path))
    if task.project_type not in {"web", "qt"}:
        result.add("task_card.invalid_project_type", "error", f"unknown project_type: {task.project_type}", str(task.path))
    if task.status == "AWAITING_HUMAN" and not task.pre_awaiting_human_node:
        result.add(
            "task_card.missing_human_resume_node",
            "error",
            "AWAITING_HUMAN requires pre_awaiting_human_node for GraphFlow resume",
            str(task.path),
        )
    for item in task.done_items:
        if not item.id or not item.check or not item.expect:
            result.add("done_item.invalid", "error", f"incomplete Done item: {item.id}", str(task.path))
        if item.status not in {"pending", "passed", "failed"}:
            result.add("done_item.invalid_status", "error", f"{item.id} has invalid status: {item.status}", str(task.path))
    return result


def validate_done_transition(task: TaskCard, artifacts: ArtifactManager) -> ValidationResult:
    result = validate_task_card(task)
    if task.status != "DONE":
        return result

    for name in artifacts.missing_for_done(task):
        path = artifacts.artifact_path(task, name)
        result.add("done.missing_artifact", "critical", f"DONE requires artifact: {name}", str(path))

    for agent in missing_required_agents(task):
        result.add("done.missing_agent", "critical", f"DONE requires executed agent: {agent}", str(task.path))

    pending = [item.id for item in task.done_items if item.status != "passed"]
    if pending:
        result.add("done.unpassed_items", "critical", f"DONE has unpassed Done items: {', '.join(pending)}", str(task.path))

    review = artifacts.read_json(task, "review.json")
    if review and review.get("verdict") != "APPROVE":
        result.add("done.review_not_approved", "critical", "review.json verdict must be APPROVE", str(artifacts.artifact_path(task, "review.json")))

    deploy = artifacts.read_json(task, "deploy-result.json")
    if task.phase == "development" and deploy and not nested_bool(deploy, ["deploy", "success"]):
        result.add("done.deploy_failed", "critical", "deploy-result.json deploy.success must be true", str(artifacts.artifact_path(task, "deploy-result.json")))

    env_check = artifacts.read_json(task, "env-check-result.json")
    if env_check:
        verdict = nested_value(env_check, ["env_check_result", "verdict"]) or nested_value(env_check, ["gate_result", "verdict"])
        if verdict != "PASS":
            result.add("done.env_check_failed", "critical", "env-check-result.json verdict must be PASS", str(artifacts.artifact_path(task, "env-check-result.json")))
    ui_review = artifacts.read_json(task, "ui-review.json")
    if ui_review:
        verdict = str(ui_review.get("verdict", ""))
        if verdict not in UI_REVIEW_APPROVED_VALUES:
            result.add("done.ui_review_not_approved", "critical", "ui-review.json verdict must be APPROVE", str(artifacts.artifact_path(task, "ui-review.json")))
    return result


_HUADESIGN_COLOR_SCALES = ("primary", "warning", "success", "error", "cyan", "violet", "yellow")
_HUADESIGN_NEUTRAL_KEYS = ("title", "fontR", "fontS", "placeholder", "split", "bg", "bgCard", "block")
_HUADESIGN_SPACING_KEYS = ("x1", "x2", "x3", "x4", "x6", "x10", "x16")
_HUADESIGN_FONT_KEYS = ("body", "small", "subtitle", "title", "display", "h1")
_HUADESIGN_RADIUS_KEYS = ("small", "default", "large")
_HUADESIGN_SHADOW_KEYS = ("level1", "level2", "level3", "cardHover")


def _build_huadesign_token_paths() -> set[str]:
    """与 dahua-direction/UI/tokens/hua_design_tokens.json 同步的合法 token dot-path 集合。

    确定性层自包含这份精简白名单（不读取外部 JSON），保证可测试且不耦合业务资料目录。
    hua_design_tokens.json 更新时需同步本集合。
    """
    paths: set[str] = set()
    for scale in _HUADESIGN_COLOR_SCALES:
        for i in range(1, 11):
            paths.add(f"color.{scale}.{i}")
    for key in _HUADESIGN_NEUTRAL_KEYS:
        paths.add(f"color.neutral.{key}")
    for key in _HUADESIGN_SPACING_KEYS:
        paths.add(f"spacing.{key}")
    for key in _HUADESIGN_FONT_KEYS:
        paths.add(f"font.fontSize.{key}")
    paths.add("font.figures.fontVariantNumeric")
    for key in _HUADESIGN_RADIUS_KEYS:
        paths.add(f"radius.{key}")
    for key in _HUADESIGN_SHADOW_KEYS:
        paths.add(f"shadow.{key}.boxShadow")
    paths.add("layout.resolutions.mainstream")
    paths.add("layout.resolutions.legacy")
    return paths


HUADESIGN_TOKEN_PATHS: set[str] = _build_huadesign_token_paths()


def _is_huadesign_token_ref(value: Any) -> bool:
    return isinstance(value, str) and value in HUADESIGN_TOKEN_PATHS


def _validate_huadesign_contract(design_tokens: dict[str, Any], result: ValidationResult) -> None:
    """huaDesign 契约校验。仅在 design_tokens.design_system == 'huaDesign' 时启用。

    未声明 huaDesign 时跳过（向后兼容：确定性测试用 stub 数据不携带 design_system，
    保持现有测试全绿）。声明 huaDesign 后强制：单一主色 = color.primary.6、
    所有 colors 值为 token dot-path 引用（禁止裸 hex）、spacing 引用 4n token、
    tokens_ref 指向单一数据源。
    """
    if design_tokens.get("design_system") != "huaDesign":
        return
    if not design_tokens.get("tokens_ref"):
        result.add("ui_design.hua_missing_tokens_ref", "error",
                   "huaDesign design_tokens.tokens_ref required (path to hua_design_tokens.json)")
    colors = design_tokens.get("colors")
    if not isinstance(colors, dict) or not colors:
        result.add("ui_design.hua_missing_colors", "error",
                   "huaDesign design_tokens.colors must be a non-empty object")
        return
    primary = colors.get("primary")
    if primary != "color.primary.6":
        result.add("ui_design.hua_invalid_primary", "error",
                   f"huaDesign colors.primary must be 'color.primary.6' (#1C79F4), got {primary!r}")
    for key, value in colors.items():
        if not _is_huadesign_token_ref(value):
            result.add("ui_design.hua_bare_color_value", "error",
                       f"huaDesign colors.{key} must be a token dot-path ref (e.g. 'color.neutral.bg'), got {value!r}")
    spacing = design_tokens.get("spacing")
    if isinstance(spacing, dict):
        grid_unit = spacing.get("grid_unit")
        if grid_unit is not None and not _is_huadesign_token_ref(grid_unit):
            result.add("ui_design.hua_bare_spacing", "error",
                       f"huaDesign spacing.grid_unit must be token ref, got {grid_unit!r}")
        scale = spacing.get("scale")
        if scale is not None:
            if not isinstance(scale, list):
                result.add("ui_design.hua_spacing_scale_not_list", "error",
                           "huaDesign spacing.scale must be a list of token refs")
            else:
                for item in scale:
                    if not _is_huadesign_token_ref(item):
                        result.add("ui_design.hua_bare_spacing", "error",
                                   f"huaDesign spacing.scale item must be token ref, got {item!r}")


def validate_ui_design_contract(payload: dict[str, Any]) -> ValidationResult:
    result = ValidationResult(ok=True)
    ui_design = payload.get("ui_design")
    if not isinstance(ui_design, dict):
        result.add("ui_design.missing", "error", "ui_design must be an object")
        return result

    for field in ["platform", "accessibility"]:
        if field not in ui_design:
            result.add("ui_design.missing_field", "error", f"ui_design missing field: {field}")

    platform = ui_design.get("platform")
    if platform == "web":
        for field in ["pages", "component_inventory", "design_tokens"]:
            if not ui_design.get(field):
                result.add("ui_design.missing_web_field", "error", f"web ui_design missing or empty: {field}")
        for page in ui_design.get("pages", []) or []:
            states = set(page.get("states", []) or [])
            missing_states = {"loading", "error", "empty", "success"} - states
            if missing_states:
                result.add("ui_design.missing_page_states", "error", f"{page.get('name', '<unnamed>')} missing states: {sorted(missing_states)}")
        design_tokens = ui_design.get("design_tokens")
        if isinstance(design_tokens, dict) and design_tokens:
            _validate_huadesign_contract(design_tokens, result)
    elif platform == "qt":
        for field in ["windows", "widget_inventory", "signals_slots", "qss_tokens"]:
            if not ui_design.get(field):
                result.add("ui_design.missing_qt_field", "error", f"qt ui_design missing or empty: {field}")
        qss_tokens = ui_design.get("qss_tokens")
        if isinstance(qss_tokens, dict) and qss_tokens:
            _validate_huadesign_contract(qss_tokens, result)
    else:
        result.add("ui_design.invalid_platform", "error", f"invalid ui_design.platform: {platform}")
    return result


def validate_agent_output(agent: str, payload: dict[str, Any]) -> ValidationResult:
    result = ValidationResult(ok=True)
    required = AGENT_REQUIRED_FIELDS.get(agent, BASIC_AGENT_FIELDS)
    for field in required:
        if field not in payload or payload[field] in (None, ""):
            result.add("agent_output.missing_field", "error", f"{agent} output missing field: {field}")
    if payload.get("agent") != agent:
        result.add("agent_output.agent_mismatch", "error", f"expected agent={agent}, got {payload.get('agent')}")
    if payload.get("status") not in {"success", "partial", "failed"}:
        result.add("agent_output.invalid_status", "error", f"invalid status: {payload.get('status')}")
    return result


def required_product_for_agent(agent: str) -> list[str]:
    return DYNAMIC_PRODUCT_MAP.get(agent, [])


def missing_required_agents(task: TaskCard) -> list[str]:
    missing = [agent for agent in ARCHITECTURE_REQUIRED_AGENTS if agent not in task.executed_agents_arch]
    if task.phase == "development":
        required = DEVELOPMENT_REQUIRED_BASE.copy()
        if task.project_type == "qt":
            required = [agent if agent != "frontend-dev" else "client-dev" for agent in required]
            required = [agent for agent in required if agent != "backend-dev"]
        missing.extend(agent for agent in required if agent not in task.executed_agents_subtask)
    return missing


def nested_value(payload: dict[str, Any], keys: list[str]) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def nested_bool(payload: dict[str, Any], keys: list[str]) -> bool:
    return nested_value(payload, keys) is True
