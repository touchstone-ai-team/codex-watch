"""Small tmux command wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import CommandError
from .models import Pane, WatchSession
from .util import is_uuid


def run_tmux(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["tmux", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or f"tmux {' '.join(args)} failed"
        raise CommandError(msg)
    return proc


def list_session_names() -> list[str]:
    proc = run_tmux(["list-sessions", "-F", "#{session_name}"], check=False)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def list_panes(session: str) -> list[Pane]:
    fmt = "#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_pid}\t#{pane_current_command}\t#{pane_dead}"
    proc = run_tmux(["list-panes", "-s", "-t", f"={session}", "-F", fmt])
    panes: list[Pane] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 6:
            continue
        session_name, window_index, pane_index, pane_pid, command, pane_dead = parts
        try:
            pid = int(pane_pid)
        except ValueError:
            continue
        panes.append(
            Pane(
                session_name=session_name,
                window_index=window_index,
                pane_index=pane_index,
                pane_pid=pid,
                current_command=command,
                pane_dead=pane_dead,
            )
        )
    return panes


def get_option(session: str, option: str) -> str:
    proc = run_tmux(["show-options", "-qv", "-t", session, option], check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def watcher_sessions() -> list[WatchSession]:
    out: list[WatchSession] = []
    for name in list_session_names():
        if not name.startswith("codex-watch-"):
            continue
        thread = get_option(name, "@watch_thread_id")
        if not thread and is_uuid(name.removeprefix("codex-watch-")):
            thread = name.removeprefix("codex-watch-")
        out.append(
            WatchSession(
                session_name=name,
                thread_id=thread,
                target_pane=get_option(name, "@watch_target_pane"),
                state=get_option(name, "@watch_state"),
                detail=get_option(name, "@watch_detail"),
                updated=get_option(name, "@watch_updated"),
            )
        )
    return out


def create_codex_session(session: str, workdir: Path, launcher: str) -> None:
    run_tmux(["new-session", "-d", "-s", session, "-c", str(workdir), launcher])
