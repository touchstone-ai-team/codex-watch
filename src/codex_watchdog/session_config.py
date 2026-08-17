"""Persist per-tmux-session defaults for codex-watch."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


DEFAULT_LAUNCHER = "codex"


@dataclass(frozen=True)
class SessionConfig:
    session: str
    launcher: str
    workdir: Path | None = None


def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser()
    return base / "codex-watchdog"


def _session_config_path(session: str) -> Path:
    safe_name = quote(session, safe="")
    return cache_dir() / "sessions" / f"{safe_name}.json"


def save_session_config(session: str, *, workdir: Path, launcher: str) -> Path:
    path = _session_config_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session": session,
                "workdir": str(workdir),
                "launcher": launcher,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def load_session_config(session: str) -> SessionConfig | None:
    path = _session_config_path(session)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if data.get("session") != session:
        return None

    launcher = str(data.get("launcher") or "").strip()
    if not launcher:
        return None

    workdir_text = str(data.get("workdir") or "").strip()
    return SessionConfig(
        session=session,
        launcher=launcher,
        workdir=Path(workdir_text) if workdir_text else None,
    )


def resolve_launcher(session: str, explicit: str | None = None) -> tuple[str, str]:
    if explicit:
        return explicit, "argument"

    saved = load_session_config(session)
    if saved:
        return saved.launcher, "session"

    return DEFAULT_LAUNCHER, "default"
