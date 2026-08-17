"""Wrapper around the underlying shell watchdog."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from importlib import resources
from pathlib import Path

from .errors import CommandError, DiscoveryError
from .models import Candidate, WatchSession
from .tmux import watcher_sessions
from .util import codex_home as default_codex_home
from .util import is_uuid, session_from_target


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser()
    return base / "codex-watchdog"


def _materialize_bundled_watcher() -> Path:
    target = _cache_dir() / "watch-codex-session.sh"
    target.parent.mkdir(parents=True, exist_ok=True)
    source = resources.files("codex_watchdog.bundled").joinpath("watch-codex-session.sh")
    data = source.read_bytes()
    should_write = True
    if target.exists():
        try:
            should_write = hashlib.sha256(data).hexdigest() != _sha256(target)
        except OSError:
            should_write = True
    if should_write:
        target.write_bytes(data)
    target.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return target


def resolve_watcher(codex_home: Path | None = None, override: str | None = None) -> Path:
    if override:
        path = Path(override).expanduser()
        if not path.exists():
            raise CommandError(f"watchdog script does not exist: {path}")
        if not os.access(path, os.X_OK):
            raise CommandError(f"watchdog script is not executable: {path}")
        return path

    home = codex_home or default_codex_home()
    skill_path = home / "skills" / "watch-codex-session" / "scripts" / "watch-codex-session.sh"
    if skill_path.exists() and os.access(skill_path, os.X_OK):
        return skill_path
    return _materialize_bundled_watcher()


def run_watcher(args: list[str], *, codex_home: Path, watcher: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    proc = subprocess.run(
        [str(watcher), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    return proc


def checked_watcher(args: list[str], *, codex_home: Path, watcher: Path) -> subprocess.CompletedProcess[str]:
    proc = run_watcher(args, codex_home=codex_home, watcher=watcher)
    if proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or f"watchdog command failed: {' '.join(args)}"
        raise CommandError(msg)
    return proc


def start_watchdog(
    candidate: Candidate,
    *,
    codex_home: Path,
    watcher: Path,
    launcher: str,
    base_wait: int,
    max_wait: int,
    self_test: bool,
) -> tuple[str, str]:
    if not candidate.watchable:
        raise DiscoveryError("; ".join(candidate.reasons) or "candidate is not watchable")
    assert candidate.thread_id and candidate.cwd
    if self_test:
        checked_watcher(["self-test"], codex_home=codex_home, watcher=watcher)
    start = checked_watcher(
        [
            "start",
            candidate.pane.target,
            candidate.thread_id,
            launcher,
            str(candidate.cwd),
            str(base_wait),
            str(max_wait),
        ],
        codex_home=codex_home,
        watcher=watcher,
    )
    status = run_watcher(["status", candidate.thread_id], codex_home=codex_home, watcher=watcher)
    first_line = status.stdout.splitlines()[0] if status.stdout.splitlines() else status.stderr.strip()
    return start.stdout.strip(), first_line


def doctor_watchdog(
    candidate: Candidate,
    *,
    codex_home: Path,
    watcher: Path,
    launcher: str,
) -> str:
    if not candidate.watchable:
        raise DiscoveryError("; ".join(candidate.reasons) or "candidate is not watchable")
    assert candidate.thread_id and candidate.cwd
    proc = checked_watcher(
        ["doctor", candidate.pane.target, candidate.thread_id, launcher, str(candidate.cwd)],
        codex_home=codex_home,
        watcher=watcher,
    )
    return proc.stdout.strip()


def list_watches() -> list[WatchSession]:
    return watcher_sessions()


def resolve_watch_ref(ref: str | None) -> WatchSession:
    watches = [w for w in list_watches() if w.thread_id]
    if ref:
        if is_uuid(ref):
            matches = [w for w in watches if w.thread_id.lower() == ref.lower()]
        else:
            wanted_session = session_from_target(ref)
            matches = [w for w in watches if w.target_session == wanted_session or w.session_name == ref]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise DiscoveryError(f"no watchdog matches: {ref}")
        raise DiscoveryError(f"multiple watchdogs match: {ref}")

    if len(watches) == 1:
        return watches[0]
    if not watches:
        raise DiscoveryError("no running Codex watchdogs found")
    sessions = ", ".join(w.target_session or w.thread_id for w in watches)
    raise DiscoveryError(f"multiple watchdogs found: {sessions}")


def status_watchdog(ref: str | None, *, codex_home: Path, watcher: Path) -> str:
    watch = resolve_watch_ref(ref)
    proc = checked_watcher(["status", watch.thread_id], codex_home=codex_home, watcher=watcher)
    return proc.stdout.rstrip()


def stop_watchdog(ref: str | None, *, codex_home: Path, watcher: Path) -> str:
    watch = resolve_watch_ref(ref)
    proc = checked_watcher(["stop", watch.thread_id], codex_home=codex_home, watcher=watcher)
    return proc.stdout.strip()


def copy_watcher_to(path: Path, *, codex_home: Path, override: str | None = None) -> Path:
    source = resolve_watcher(codex_home, override)
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, path)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path
