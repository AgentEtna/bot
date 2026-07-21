"""Regression test: pause state must survive a process restart.

The bug: bot_status.paused was in-memory only, while bot_status.paused_at /
resumed_at were persisted. A fly machine restart (deploy or otherwise) wiped
the in-memory bool while keeping the timestamps. If the operator paused phi
because she was misbehaving and then a deploy happened, the bot would silently
resume — exactly the wrong failure mode.

Fix: persist `paused` alongside the timestamps. These tests pin the contract.
"""

import json
from pathlib import Path

from bot import status as status_module
from bot.status import BotStatus


def _patch_status_file(monkeypatch, tmp_path: Path) -> Path:
    """Redirect STATUS_FILE to a tmp path for the duration of the test."""
    p = tmp_path / "status.json"
    monkeypatch.setattr(status_module, "STATUS_FILE", p)
    return p


def test_paused_persists_across_load(monkeypatch, tmp_path):
    f = _patch_status_file(monkeypatch, tmp_path)

    a = BotStatus()
    a.record_paused()
    assert a.paused is True
    assert f.exists(), "record_paused() should have written status.json"

    b = BotStatus()
    b._load()
    assert b.paused is True, (
        "paused must survive a process restart — if False here, a deploy "
        "would silently resume a paused bot"
    )
    assert b.paused_at is not None


def test_resumed_persists_across_load(monkeypatch, tmp_path):
    _patch_status_file(monkeypatch, tmp_path)

    a = BotStatus()
    a.record_paused()
    a.record_resumed()
    assert a.paused is False

    b = BotStatus()
    b._load()
    assert b.paused is False
    assert b.resumed_at is not None


def test_fresh_status_defaults_to_not_paused(monkeypatch, tmp_path):
    """No file on disk → default paused=False (safe — operator can pause if needed)."""
    _patch_status_file(monkeypatch, tmp_path)
    s = BotStatus()
    s._load()
    assert s.paused is False


def test_corrupt_file_falls_back_to_default(monkeypatch, tmp_path):
    f = _patch_status_file(monkeypatch, tmp_path)
    f.write_text("not json {")
    s = BotStatus()
    s._load()
    # graceful degradation — don't crash, default to not-paused
    assert s.paused is False


def test_persisted_format_includes_paused_key(monkeypatch, tmp_path):
    """Pin the on-disk schema so accidental rename can't silently break this again."""
    f = _patch_status_file(monkeypatch, tmp_path)
    s = BotStatus()
    s.record_paused()
    data = json.loads(f.read_text())
    assert data.get("paused") is True
    assert data.get("paused_at") is not None


def test_workflow_failure_ids_persist_and_deduplicate(monkeypatch, tmp_path):
    _patch_status_file(monkeypatch, tmp_path)
    status = BotStatus()
    status.record_workflow_failures(["run-a", "run-b", "run-a"])

    restored = BotStatus()
    restored._load()
    assert restored.workflow_failure_monitor_seeded is True
    assert restored.workflow_failure_run_ids == ["run-a", "run-b"]


def test_empty_workflow_failure_seed_persists(monkeypatch, tmp_path):
    _patch_status_file(monkeypatch, tmp_path)
    status = BotStatus()
    status.record_workflow_failures([])

    restored = BotStatus()
    restored._load()
    assert restored.workflow_failure_monitor_seeded is True
    assert restored.workflow_failure_run_ids == []
