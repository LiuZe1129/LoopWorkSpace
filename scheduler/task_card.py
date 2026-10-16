from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from .state import DoneItem, TaskCard


FRONTMATTER_RE = re.compile(r"\A---\n(?P<frontmatter>.*?)\n---\n(?P<body>.*)\Z", re.S)


def load_task_card(path: Path) -> TaskCard:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError(f"{path} does not contain YAML-style frontmatter")

    frontmatter = parse_frontmatter(match.group("frontmatter"))
    body = match.group("body")
    title = extract_title(body)
    done_items = parse_done_items(body)
    return TaskCard(path=path, raw_frontmatter=frontmatter, title=title, done_items=done_items, body=body)


def find_task_cards(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for base in [root / ".loop" / "active", root / ".loop" / "done"]:
        if not base.exists():
            continue
        candidates.extend(p for p in base.rglob("task-*.md") if p.is_file())
    return sorted(candidates)


def parse_frontmatter(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = parse_scalar(value.strip())
    return data


def parse_scalar(value: str) -> Any:
    value = strip_inline_comment(value).strip()
    if value in {"", "null", "None"}:
        return None
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return []
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value.strip("\"'")


def strip_inline_comment(value: str) -> str:
    in_quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            in_quote = None if in_quote == char else char
        if char == "#" and in_quote is None:
            return value[:index]
    return value


def extract_title(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def parse_done_items(body: str) -> list[DoneItem]:
    lines = body.splitlines()
    in_done = False
    current: dict[str, str] | None = None
    items: list[DoneItem] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_done and current:
                items.append(done_item_from_dict(current))
                current = None
            in_done = stripped.startswith("## Done")
            continue
        if not in_done:
            continue
        if stripped.startswith("- id:"):
            if current:
                items.append(done_item_from_dict(current))
            current = {"id": parse_scalar(stripped.split(":", 1)[1])}
            continue
        if current is not None and re.match(r"^(check|expect|status|gate_stage):", stripped):
            key, value = stripped.split(":", 1)
            current[key] = parse_scalar(value.strip())

    if in_done and current:
        items.append(done_item_from_dict(current))
    return items


def done_item_from_dict(data: dict[str, Any]) -> DoneItem:
    return DoneItem(
        id=str(data.get("id", "")),
        check=str(data.get("check", "")),
        expect=str(data.get("expect", "")),
        status=str(data.get("status", "pending")),
        gate_stage=str(data.get("gate_stage", "final") or "final"),
    )

