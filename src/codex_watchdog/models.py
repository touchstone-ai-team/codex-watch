"""Small data models used by discovery and CLI output."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Pane:
    session_name: str
    window_index: str
    pane_index: str
    pane_pid: int
    current_command: str
    pane_dead: str

    @property
    def target(self) -> str:
        return f"{self.session_name}:{self.window_index}.{self.pane_index}"

    @property
    def is_dedicated_target(self) -> bool:
        return self.window_index == "0" and self.pane_index == "0"

    @property
    def is_alive(self) -> bool:
        return self.pane_dead == "0"


@dataclass
class Candidate:
    pane: Pane
    thread_id: str | None = None
    rollout_path: Path | None = None
    cwd: Path | None = None
    source: str | None = None
    goal_status: str | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def watchable(self) -> bool:
        return not self.reasons and bool(self.thread_id and self.rollout_path and self.cwd)

    def display_row(self) -> dict[str, str]:
        return {
            "session": self.pane.session_name,
            "pane": self.pane.target,
            "thread": self.thread_id or "-",
            "goal": self.goal_status or "-",
            "cwd": str(self.cwd or "-"),
            "status": "watchable" if self.watchable else "blocked",
            "reason": "; ".join(self.reasons),
        }


@dataclass(frozen=True)
class WatchSession:
    session_name: str
    thread_id: str
    target_pane: str
    state: str
    detail: str
    updated: str

    @property
    def target_session(self) -> str:
        return self.target_pane.split(":", 1)[0] if self.target_pane else ""

    def display_row(self) -> dict[str, str]:
        return {
            "watcher": self.session_name,
            "thread": self.thread_id,
            "target": self.target_pane or "-",
            "state": self.state or "-",
            "detail": self.detail or "-",
            "updated": self.updated or "-",
        }
