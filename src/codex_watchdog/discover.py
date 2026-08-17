"""Discover a safe Codex root thread from a live tmux pane."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Iterable

from .errors import DiscoveryError
from .models import Candidate, Pane
from .tmux import list_panes, list_session_names
from .util import codex_home as default_codex_home
from .util import normalize_pane_target, rollout_uuid, session_from_target

CODEX_PROCESS_NAMES = {"codex", "node"}
WATCHABLE_GOALS = {"active", "blocked"}
TERMINAL_GOALS = {"paused", "complete", "usage_limited", "budget_limited"}


def ps_rows() -> list[tuple[int, int]]:
    proc = subprocess.run(
        ["ps", "-eo", "pid=,ppid="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    rows: list[tuple[int, int]] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1])))
        except ValueError:
            continue
    return rows


def descendant_pids(root_pid: int, rows: Iterable[tuple[int, int]] | None = None) -> set[int]:
    rows = list(rows if rows is not None else ps_rows())
    known = {int(root_pid)}
    changed = True
    while changed:
        changed = False
        for pid, ppid in rows:
            if ppid in known and pid not in known:
                known.add(pid)
                changed = True
    return known


def _clean_fd_target(raw: str) -> str:
    suffix = " (deleted)"
    return raw[:-len(suffix)] if raw.endswith(suffix) else raw


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def rollouts_opened_by_process_tree(pane_pid: int, codex_home: Path) -> list[Path]:
    sessions_dir = codex_home / "sessions"
    found: set[Path] = set()
    for pid in descendant_pids(pane_pid):
        fd_dir = Path("/proc") / str(pid) / "fd"
        if not fd_dir.exists():
            continue
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = Path(_clean_fd_target(os.readlink(fd)))
            except OSError:
                continue
            if target.suffix != ".jsonl":
                continue
            if rollout_uuid(target) and _is_under(target, sessions_dir):
                found.add(target)
    return sorted(found)


def parse_session_meta(path: Path, *, expected_thread_id: str | None = None) -> dict[str, str] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("type") != "session_meta":
                    continue
                payload = item.get("payload") or {}
                thread_id = str(payload.get("id") or "")
                if expected_thread_id and thread_id.lower() != expected_thread_id.lower():
                    continue
                return {
                    "id": thread_id.lower(),
                    "source": str(payload.get("source") or ""),
                    "cwd": str(payload.get("cwd") or ""),
                }
    except OSError:
        return None
    return None


def read_goal_status(thread_id: str, codex_home: Path) -> str:
    db_path = codex_home / "goals_1.sqlite"
    if not db_path.exists():
        return "missing"
    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            row = conn.execute(
                "SELECT status FROM thread_goals WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return "missing"
    return str(row[0]) if row else "missing"


def discover_pane(pane: Pane, codex_home: Path | None = None) -> Candidate:
    home = codex_home or default_codex_home()
    candidate = Candidate(pane=pane)

    if not pane.is_dedicated_target:
        candidate.reasons.append("target must be SESSION:0.0")
    if not pane.is_alive:
        candidate.reasons.append("pane is dead")
    if pane.current_command not in CODEX_PROCESS_NAMES:
        candidate.reasons.append(f"pane command is not Codex: {pane.current_command or '-'}")

    if candidate.reasons:
        return candidate

    rollouts = rollouts_opened_by_process_tree(pane.pane_pid, home)
    metas: list[tuple[Path, dict[str, str]]] = []
    for path in rollouts:
        thread_id = rollout_uuid(path)
        if not thread_id:
            continue
        meta = parse_session_meta(path, expected_thread_id=thread_id)
        if meta and meta.get("source") == "cli":
            metas.append((path, meta))

    if not metas:
        candidate.reasons.append(
            "Codex has not opened a root rollout yet; attach to this tmux session, "
            "submit a real task, and create or resume an active Goal"
        )
        return candidate
    if len(metas) > 1:
        ids = ", ".join(meta["id"] for _, meta in metas)
        candidate.reasons.append(f"multiple root CLI rollouts opened: {ids}")
        return candidate

    rollout_path, meta = metas[0]
    candidate.thread_id = meta["id"]
    candidate.rollout_path = rollout_path
    candidate.source = meta.get("source")

    cwd_text = meta.get("cwd") or ""
    if not cwd_text:
        candidate.reasons.append("rollout has no saved cwd")
    else:
        candidate.cwd = Path(cwd_text)
        if not candidate.cwd.exists():
            candidate.reasons.append(f"saved cwd does not exist: {candidate.cwd}")

    candidate.goal_status = read_goal_status(candidate.thread_id, home)
    if candidate.goal_status not in WATCHABLE_GOALS:
        if candidate.goal_status in TERMINAL_GOALS:
            candidate.reasons.append(f"Goal is terminal: {candidate.goal_status}")
        elif candidate.goal_status == "missing":
            candidate.reasons.append(
                "Codex thread found, but no native Goal exists for this thread; "
                "run /goal <objective> in the TUI before enabling watchdog"
            )
        else:
            candidate.reasons.append(f"Goal must be active or blocked, got: {candidate.goal_status}")

    return candidate


def discover_candidates(target: str | None = None, codex_home: Path | None = None) -> list[Candidate]:
    home = codex_home or default_codex_home()
    candidates: list[Candidate] = []
    if target:
        session = session_from_target(target)
        panes = list_panes(session)
        if len(panes) != 1:
            pane = Pane(session, "0", "0", 0, "", "1")
            return [Candidate(pane=pane, reasons=[f"session must contain exactly one pane, got {len(panes)}"])]
        expected = normalize_pane_target(target)
        if panes[0].target != expected:
            return [Candidate(pane=panes[0], reasons=[f"target pane not found: {expected}"])]
        return [discover_pane(panes[0], home)]

    for session in list_session_names():
        if session.startswith("codex-watch-"):
            continue
        panes = list_panes(session)
        if len(panes) != 1:
            continue
        candidates.append(discover_pane(panes[0], home))
    return candidates


def select_unique_watchable(candidates: list[Candidate]) -> Candidate:
    watchable = [c for c in candidates if c.watchable]
    if len(watchable) == 1:
        return watchable[0]
    if not watchable:
        raise DiscoveryError("no watchable Codex tmux session found")
    sessions = ", ".join(c.pane.session_name for c in watchable)
    raise DiscoveryError(f"multiple watchable Codex sessions found: {sessions}")
