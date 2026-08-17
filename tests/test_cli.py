from __future__ import annotations

from codex_watchdog.cli import _normalize_argv, _parser


def test_cli_aliases() -> None:
    assert _normalize_argv(["start"]) == ["on"]
    assert _normalize_argv(["up", "work"]) == ["on", "work"]
    assert _normalize_argv(["stop", "work"]) == ["off", "work"]
    assert _normalize_argv(["st"]) == ["status"]
    assert _normalize_argv(["list"]) == ["ls"]
    assert _normalize_argv(["doctor"]) == ["doctor"]


def test_launcher_defaults_are_contextual() -> None:
    parser = _parser()

    on_args = parser.parse_args(["on", "work"])
    assert on_args.launcher is None

    doctor_args = parser.parse_args(["doctor", "work"])
    assert doctor_args.launcher is None

    new_args = parser.parse_args(["new", "work"])
    assert new_args.launcher == "codex"
