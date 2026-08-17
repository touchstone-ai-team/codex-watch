from __future__ import annotations

from codex_watchdog.cli import _normalize_argv


def test_cli_aliases() -> None:
    assert _normalize_argv(["start"]) == ["on"]
    assert _normalize_argv(["up", "work"]) == ["on", "work"]
    assert _normalize_argv(["stop", "work"]) == ["off", "work"]
    assert _normalize_argv(["st"]) == ["status"]
    assert _normalize_argv(["list"]) == ["ls"]
    assert _normalize_argv(["doctor"]) == ["doctor"]
