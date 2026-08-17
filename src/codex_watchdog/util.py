"""Shared helpers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
UUID_IN_ROLLOUT_RE = re.compile(
    r"(?:^|-)([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)


def is_uuid(value: str | None) -> bool:
    return bool(value and UUID_RE.fullmatch(value))


def normalize_pane_target(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError("empty tmux target")
    if ":" in value:
        return value
    return f"{value}:0.0"


def session_from_target(value: str) -> str:
    return normalize_pane_target(value).split(":", 1)[0]


def codex_home(raw: str | None = None) -> Path:
    return Path(raw or os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def rollout_uuid(path: Path) -> str | None:
    match = UUID_IN_ROLLOUT_RE.search(path.name)
    return match.group(1).lower() if match else None


def json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=json_default))
