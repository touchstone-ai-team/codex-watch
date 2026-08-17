from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from codex_watchdog.discover import (
    WATCHABLE_GOALS,
    descendant_pids,
    discover_pane,
    parse_session_meta,
    read_goal_status,
    select_unique_watchable,
)
from codex_watchdog.errors import DiscoveryError
from codex_watchdog.models import Candidate, Pane
from codex_watchdog.util import is_uuid, normalize_pane_target, rollout_uuid, session_from_target


THREAD = "01234567-89ab-cdef-0123-456789abcdef"


def pane(name: str = "work") -> Pane:
    return Pane(
        session_name=name,
        window_index="0",
        pane_index="0",
        pane_pid=100,
        current_command="codex",
        pane_dead="0",
    )


def test_uuid_and_target_helpers() -> None:
    assert is_uuid(THREAD)
    assert not is_uuid("latest")
    assert normalize_pane_target("work") == "work:0.0"
    assert normalize_pane_target("work:0.0") == "work:0.0"
    assert session_from_target("work:0.0") == "work"
    assert rollout_uuid(Path(f"2026-08-17-{THREAD}.jsonl")) == THREAD
    assert rollout_uuid(Path("not-a-rollout.jsonl")) is None


def test_descendant_pids_walks_complete_tree() -> None:
    rows = [
        (100, 1),
        (101, 100),
        (102, 101),
        (200, 1),
        (201, 200),
    ]
    assert descendant_pids(100, rows) == {100, 101, 102}


def test_parse_session_meta_uses_expected_thread_id(tmp_path: Path) -> None:
    rollout = tmp_path / f"rollout-{THREAD}.jsonl"
    rollout.write_text(
        "\n".join(
            [
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "x"}}),
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": THREAD,
                            "source": "cli",
                            "cwd": "/tmp/work",
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    assert parse_session_meta(rollout, expected_thread_id=THREAD) == {
        "id": THREAD,
        "source": "cli",
        "cwd": "/tmp/work",
    }
    assert parse_session_meta(rollout, expected_thread_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa") is None


def test_read_goal_status(tmp_path: Path) -> None:
    db = tmp_path / "goals_1.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE thread_goals (thread_id TEXT PRIMARY KEY, status TEXT)")
    conn.execute("INSERT INTO thread_goals VALUES (?, ?)", (THREAD, "active"))
    conn.commit()
    conn.close()
    assert read_goal_status(THREAD, tmp_path) == "active"
    assert read_goal_status("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", tmp_path) == "missing"
    assert WATCHABLE_GOALS == {"active", "blocked"}


def test_discover_pane_missing_goal_reason_is_specific(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    rollout = tmp_path / "sessions" / "2026" / "08" / "17" / f"rollout-2026-08-17T00-00-00-{THREAD}.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": THREAD,
                    "source": "cli",
                    "cwd": str(workdir),
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("codex_watchdog.discover.rollouts_opened_by_process_tree", lambda *_args: [rollout])
    c = discover_pane(pane(), tmp_path)

    assert not c.watchable
    assert c.thread_id == THREAD
    assert c.goal_status == "missing"
    assert "no native Goal exists" in c.display_row()["reason"]
    assert "/goal <objective>" in c.display_row()["reason"]


def test_select_unique_watchable() -> None:
    c = Candidate(pane=pane(), thread_id=THREAD, rollout_path=Path("/tmp/r.jsonl"), cwd=Path("/tmp"), goal_status="active")
    assert select_unique_watchable([c]) is c


def test_select_unique_watchable_rejects_none_and_many() -> None:
    blocked = Candidate(pane=pane(), reasons=["Goal missing"])
    with pytest.raises(DiscoveryError):
        select_unique_watchable([blocked])

    c1 = Candidate(pane=pane("a"), thread_id=THREAD, rollout_path=Path("/tmp/a.jsonl"), cwd=Path("/tmp"), goal_status="active")
    c2 = Candidate(
        pane=pane("b"),
        thread_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        rollout_path=Path("/tmp/b.jsonl"),
        cwd=Path("/tmp"),
        goal_status="blocked",
    )
    with pytest.raises(DiscoveryError):
        select_unique_watchable([c1, c2])
