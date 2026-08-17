from __future__ import annotations

from pathlib import Path

from codex_watchdog.session_config import load_session_config, resolve_launcher, save_session_config


def test_session_config_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    saved_path = save_session_config(
        "touchstone/tests",
        workdir=Path("/repo"),
        launcher="codex-codexcn",
    )

    assert saved_path.parent == tmp_path / "codex-watchdog" / "sessions"
    assert saved_path.name == "touchstone%2Ftests.json"

    loaded = load_session_config("touchstone/tests")
    assert loaded is not None
    assert loaded.session == "touchstone/tests"
    assert loaded.workdir == Path("/repo")
    assert loaded.launcher == "codex-codexcn"


def test_resolve_launcher_precedence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    save_session_config("work", workdir=Path("/repo"), launcher="codex-codexcn")

    assert resolve_launcher("work", explicit="codex-other") == ("codex-other", "argument")
    assert resolve_launcher("work") == ("codex-codexcn", "session")
    assert resolve_launcher("unknown") == ("codex", "default")
