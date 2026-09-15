from __future__ import annotations

from codex_watchdog.cli import _normalize_argv, _parser
from codex_watchdog import watcher


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


def test_default_watcher_uses_versioned_bundled_copy(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(watcher, "_cache_dir", lambda: tmp_path)
    stale_skill = tmp_path / "codex-home" / "skills" / "watch-codex-session" / "scripts" / "watch-codex-session.sh"
    stale_skill.parent.mkdir(parents=True)
    stale_skill.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    stale_skill.chmod(0o700)

    resolved = watcher.resolve_watcher(tmp_path / "codex-home")

    assert resolved == tmp_path / "watch-codex-session.sh"
    assert "root_retryable_provider_state" in resolved.read_text(encoding="utf-8")
