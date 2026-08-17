"""Command line interface for codex-watch."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .discover import discover_candidates, select_unique_watchable
from .errors import CommandError, DiscoveryError, WatchdogError
from .session_config import DEFAULT_LAUNCHER
from .session_config import resolve_launcher as resolve_session_launcher
from .session_config import save_session_config
from .tmux import create_codex_session
from .util import codex_home as default_codex_home
from .util import print_json
from .watcher import (
    doctor_watchdog,
    list_watches,
    resolve_watcher,
    start_watchdog,
    status_watchdog,
    stop_watchdog,
)

ALIASES = {
    "start": "on",
    "up": "on",
    "watch": "on",
    "stop": "off",
    "down": "off",
    "rm": "off",
    "destroy": "off",
    "st": "status",
    "list": "ls",
}


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an integer: {value}") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codex-watch",
        description="Auto-discover and control the tmux watchdog for Codex CLI sessions.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--codex-home", default=None, help="Default: $CODEX_HOME or ~/.codex")
        sp.add_argument("--watcher", default=None, help="Override watch-codex-session.sh path")
        sp.add_argument("--json", action="store_true", help="Print JSON output")

    on = sub.add_parser("on", help="Start a watchdog for the unique or named Codex tmux session")
    on.add_argument("target", nargs="?", help="Optional tmux session name or SESSION:0.0 target")
    on.add_argument("--launcher", default=None, help="Override launcher; defaults to launcher saved by `new`, then codex")
    on.add_argument("--base-wait", type=_positive_int, default=120)
    on.add_argument("--max-wait", type=_positive_int, default=3600)
    on.add_argument("--no-self-test", action="store_true")
    common(on)

    doctor = sub.add_parser("doctor", help="Explain what Codex tmux session would be watched")
    doctor.add_argument("target", nargs="?", help="Optional tmux session name or SESSION:0.0 target")
    doctor.add_argument("--launcher", default=None, help="Override launcher; defaults to launcher saved by `new`, then codex")
    common(doctor)

    status = sub.add_parser("status", help="Show watchdog status")
    status.add_argument("ref", nargs="?", help="Optional tmux session name or thread UUID")
    status.add_argument("--one-line", action="store_true")
    common(status)

    off = sub.add_parser("off", help="Stop a watchdog without killing the target Codex session")
    off.add_argument("ref", nargs="?", help="Optional tmux session name or thread UUID")
    common(off)

    ls_cmd = sub.add_parser("ls", help="List running watchdog sessions")
    common(ls_cmd)

    self_test = sub.add_parser("self-test", help="Run the underlying watchdog self-test")
    common(self_test)

    new = sub.add_parser("new", help="Create a dedicated tmux session running Codex")
    new.add_argument("session", help="New tmux session name")
    new.add_argument("--workdir", default=str(Path.cwd()))
    new.add_argument("--launcher", default=DEFAULT_LAUNCHER)
    new.add_argument("--json", action="store_true")

    return p


def _normalize_argv(argv: list[str]) -> list[str]:
    if argv and argv[0] in ALIASES:
        return [ALIASES[argv[0]], *argv[1:]]
    return argv


def _rows(candidates: list[Any]) -> list[dict[str, str]]:
    return [c.display_row() for c in candidates]


def _print_candidate_table(candidates) -> None:
    rows = _rows(candidates)
    if not rows:
        print("No Codex tmux sessions discovered.")
        return
    widths = {
        "session": max(len("session"), *(len(r["session"]) for r in rows)),
        "thread": max(len("thread"), *(len(r["thread"]) for r in rows)),
        "goal": max(len("goal"), *(len(r["goal"]) for r in rows)),
        "status": max(len("status"), *(len(r["status"]) for r in rows)),
    }
    print(f"{'session':<{widths['session']}}  {'thread':<{widths['thread']}}  {'goal':<{widths['goal']}}  {'status':<{widths['status']}}  cwd / reason")
    for r in rows:
        tail = r["cwd"] if r["status"] == "watchable" else r["reason"]
        print(
            f"{r['session']:<{widths['session']}}  "
            f"{r['thread']:<{widths['thread']}}  "
            f"{r['goal']:<{widths['goal']}}  "
            f"{r['status']:<{widths['status']}}  "
            f"{tail}"
        )


def _print_watch_table(watches) -> None:
    rows = _rows(watches)
    if not rows:
        print("No running Codex watchdogs.")
        return
    widths = {
        "target": max(len("target"), *(len(r["target"]) for r in rows)),
        "state": max(len("state"), *(len(r["state"]) for r in rows)),
        "thread": max(len("thread"), *(len(r["thread"]) for r in rows)),
    }
    print(f"{'target':<{widths['target']}}  {'state':<{widths['state']}}  {'thread':<{widths['thread']}}  detail")
    for r in rows:
        print(f"{r['target']:<{widths['target']}}  {r['state']:<{widths['state']}}  {r['thread']:<{widths['thread']}}  {r['detail']}")


def _cmd_on(args: argparse.Namespace) -> int:
    home = default_codex_home(args.codex_home)
    watcher = resolve_watcher(home, args.watcher)
    candidates = discover_candidates(args.target, home)
    try:
        candidate = select_unique_watchable(candidates)
    except DiscoveryError:
        if args.json:
            print_json({"ok": False, "candidates": _rows(candidates)})
        else:
            _print_candidate_table(candidates)
            print()
            if args.target:
                print("Target is not watchable yet.")
                print(
                    "If goal=missing, Codex is running a normal thread/turn, not a native Goal; "
                    "run /goal <objective> in the TUI, then rerun this command."
                )
            else:
                print("Specify a session, for example: codex-watch on <tmux-session>")
        return 1
    if args.base_wait > args.max_wait:
        raise CommandError("--base-wait must be <= --max-wait")
    launcher, launcher_source = resolve_session_launcher(candidate.pane.session_name, args.launcher)
    start_line, status_line = start_watchdog(
        candidate,
        codex_home=home,
        watcher=watcher,
        launcher=launcher,
        base_wait=args.base_wait,
        max_wait=args.max_wait,
        self_test=not args.no_self_test,
    )
    if args.json:
        print_json(
            {
                "ok": True,
                "session": candidate.pane.session_name,
                "pane": candidate.pane.target,
                "thread": candidate.thread_id,
                "cwd": candidate.cwd,
                "launcher": launcher,
                "launcher_source": launcher_source,
                "start": start_line,
                "status": status_line,
            }
        )
    else:
        print(
            f"FOUND session={candidate.pane.session_name} pane={candidate.pane.target} "
            f"thread={candidate.thread_id} goal={candidate.goal_status} "
            f"launcher={launcher} ({launcher_source})"
        )
        if start_line:
            print(start_line)
        if status_line:
            print(status_line)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    home = default_codex_home(args.codex_home)
    watcher = resolve_watcher(home, args.watcher)
    candidates = discover_candidates(args.target, home)
    candidate = select_unique_watchable(candidates)
    launcher, launcher_source = resolve_session_launcher(candidate.pane.session_name, args.launcher)
    out = doctor_watchdog(candidate, codex_home=home, watcher=watcher, launcher=launcher)
    if args.json:
        print_json(
            {
                "ok": True,
                "candidate": candidate.display_row(),
                "launcher": launcher,
                "launcher_source": launcher_source,
                "doctor": out,
            }
        )
    else:
        print(out)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    home = default_codex_home(args.codex_home)
    watcher = resolve_watcher(home, args.watcher)
    out = status_watchdog(args.ref, codex_home=home, watcher=watcher)
    if args.json:
        first = out.splitlines()[0] if out else ""
        print_json({"ok": True, "status": first, "raw": out})
    elif args.one_line:
        print(out.splitlines()[0] if out else "")
    else:
        print(out)
    return 0


def _cmd_off(args: argparse.Namespace) -> int:
    home = default_codex_home(args.codex_home)
    watcher = resolve_watcher(home, args.watcher)
    out = stop_watchdog(args.ref, codex_home=home, watcher=watcher)
    if args.json:
        print_json({"ok": True, "message": out})
    else:
        print(out)
    return 0


def _cmd_ls(args: argparse.Namespace) -> int:
    watches = list_watches()
    if args.json:
        print_json({"watches": _rows(watches)})
    else:
        _print_watch_table(watches)
    return 0


def _cmd_self_test(args: argparse.Namespace) -> int:
    home = default_codex_home(args.codex_home)
    watcher = resolve_watcher(home, args.watcher)
    from .watcher import checked_watcher

    out = checked_watcher(["self-test"], codex_home=home, watcher=watcher).stdout.strip()
    if args.json:
        print_json({"ok": True, "message": out})
    else:
        print(out)
    return 0


def _cmd_new(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    if not workdir.exists():
        raise CommandError(f"workdir does not exist: {workdir}")
    create_codex_session(args.session, workdir, args.launcher)
    config_path = save_session_config(args.session, workdir=workdir, launcher=args.launcher)
    if args.json:
        print_json(
            {
                "ok": True,
                "session": args.session,
                "workdir": workdir,
                "launcher": args.launcher,
                "config": config_path,
                "next": [
                    f"tmux attach -t {args.session}",
                    "create or resume a Codex Goal",
                    f"codex-watch on {args.session}",
                ],
            }
        )
    else:
        print(f"CREATED tmux session={args.session} workdir={workdir} launcher={args.launcher}")
        print(f"Saved launcher metadata: {config_path}")
        print(f"Next: tmux attach -t {args.session}")
        print("Inside Codex, submit a real task and create or resume an active Goal.")
        print("Wait until Codex is Working or Goal blocked, detach, then run:")
        print(f"  codex-watch on {args.session}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(_normalize_argv(list(sys.argv[1:] if argv is None else argv)))
    try:
        if args.command == "on":
            return _cmd_on(args)
        if args.command == "doctor":
            return _cmd_doctor(args)
        if args.command == "status":
            return _cmd_status(args)
        if args.command == "off":
            return _cmd_off(args)
        if args.command == "ls":
            return _cmd_ls(args)
        if args.command == "self-test":
            return _cmd_self_test(args)
        if args.command == "new":
            return _cmd_new(args)
    except WatchdogError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
