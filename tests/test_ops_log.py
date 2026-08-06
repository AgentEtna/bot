"""Ops log: jetstream events → durable rows → [RECENT OPERATIONS].

The regression these tests guard: [RECENT OPERATIONS] used to be a
listRecords snapshot bounded by TOP_N=10 rows — deletes were invisible,
edits looked like creates, and on a busy day the "window" was a few hours,
which is how phi re-posted a 24h-old subject verbatim (gracekind,
2026-08-05/06 22:02).
"""

import json
import time

import pytest

from bot.core import ops_log
from bot.core.recent_operations import _merge, _render, _rows_from_ops


@pytest.fixture(autouse=True)
def _tmp_log(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ops_log.settings, "ops_log_path", str(tmp_path / "ops_log.jsonl")
    )
    ops_log._local_writes.clear()


def _event(op, nsid="app.bsky.feed.post", rkey="3abc", record=None, time_us=None):
    return {
        "did": "did:plc:x",
        "kind": "commit",
        "time_us": time_us or int(time.time() * 1_000_000),
        "commit": {
            "rev": "r",
            "operation": op,
            "collection": nsid,
            "rkey": rkey,
            **({"record": record} if record is not None else {}),
        },
    }


def test_event_to_row_create_keeps_post_record():
    row = ops_log.event_to_row(_event("create", record={"text": "hi"}))
    assert row is not None
    assert row["op"] == "create"
    assert row["record"] == {"text": "hi"}


def test_event_to_row_drops_bodies_for_noise_collections():
    row = ops_log.event_to_row(
        _event("create", nsid="app.bsky.feed.like", record={"subject": {}})
    )
    assert row is not None
    assert row["record"] is None


def test_event_to_row_ignores_non_commit():
    assert ops_log.event_to_row({"kind": "identity"}) is None


def test_append_read_window_and_cursor():
    old = ops_log.event_to_row(
        _event("create", rkey="3old", record={"text": "old"},
               time_us=int((time.time() - 72 * 3600) * 1_000_000))
    )
    new = ops_log.event_to_row(_event("create", rkey="3new", record={"text": "new"}))
    assert old and new
    ops_log.append_op(old)
    ops_log.append_op(new)
    rows = ops_log.read_ops(window_hours=48)
    assert [r["rkey"] for r in rows] == ["3new"]
    assert ops_log.last_cursor_us() == new["time_us"]


def test_read_ops_tolerates_torn_tail_write():
    row = ops_log.event_to_row(_event("create", record={"text": "ok"}))
    assert row
    ops_log.append_op(row)
    with open(ops_log.settings.ops_log_path, "a") as f:
        f.write('{"time_us": 123, "truncat')
    assert [r["rkey"] for r in ops_log.read_ops(48)] == ["3abc"]


def test_prune_drops_expired_rows():
    ancient = ops_log.event_to_row(
        _event("create", rkey="3anc", record={"text": "x"},
               time_us=int((time.time() - 30 * 86400) * 1_000_000))
    )
    fresh = ops_log.event_to_row(_event("create", rkey="3fresh", record={"text": "y"}))
    assert ancient and fresh
    ops_log.append_op(ancient)
    ops_log.append_op(fresh)
    ops_log.prune_log()
    content = open(ops_log.settings.ops_log_path).read()
    assert "3fresh" in content and "3anc" not in content


def test_local_write_attribution():
    ops_log.record_local_write("at://did:plc:x/app.bsky.feed.post/3abc")
    row = ops_log.event_to_row(_event("create", record={"text": "mine"}))
    assert row and row["local"] is True
    other = ops_log.event_to_row(_event("delete", rkey="3zzz"))
    assert other and other["local"] is False


# --- delete visibility through [RECENT OPERATIONS] ---


def _op(op, rkey, nsid="network.cosmik.card", record=None, offset_s=0):
    t = int((time.time() + offset_s) * 1_000_000)
    return ops_log.OpRow(
        time_us=t,
        at=ops_log._iso_from_us(t),
        op=op,
        nsid=nsid,
        rkey=rkey,
        local=False,
        record=record,
    )


def test_external_delete_is_visible_and_flagged():
    """The semble tripwire: an external service deleting phi's cards must
    show up, attributed as not-this-process — the old snapshot rendered
    nothing at all for a delete."""
    rows = _rows_from_ops(
        [
            _op("create", "3card", record={"type": "NOTE"}),
            _op("delete", "3card", offset_s=60),
        ]
    )
    block = _render(rows)
    assert "DELETED (not via this process)" in block
    assert "was: NOTE card" in block  # phi sees WHAT vanished


def test_edit_renders_as_edit():
    rows = _rows_from_ops(
        [
            _op(
                "update",
                "3p",
                nsid="app.bsky.feed.post",
                record={"text": "second thoughts"},
            )
        ]
    )
    assert "EDITED" in _render(rows)


def test_merge_prefers_event_rows_and_backfills_snapshot():
    event_rows = _rows_from_ops(
        [_op("create", "3a", nsid="app.bsky.feed.post", record={"text": "live"})]
    )
    snapshot = [
        dict(rkey="3a", nsid="app.bsky.feed.post", created_at="2026-08-06T00:00:00",
             summary="stale", op="create", local=False),
        dict(rkey="3b", nsid="app.bsky.feed.post", created_at="2026-08-06T00:00:01",
             summary="gap-fill", op="create", local=False),
    ]
    merged = _merge(event_rows, snapshot)  # type: ignore[arg-type]
    summaries = [r["summary"] for r in merged]
    assert "gap-fill" in summaries
    assert "stale" not in summaries


def test_window_announces_truncation():
    rows = _rows_from_ops(
        [
            _op("create", f"3r{i}", nsid="app.bsky.feed.post", record={"text": str(i)},
                offset_s=i)
            for i in range(5)
        ]
    )
    block = _render(rows[-3:], truncated=2)
    assert "2 older rows elided" in block


def test_op_rows_roundtrip_through_jsonl():
    row = ops_log.event_to_row(_event("create", record={"text": "persist me"}))
    assert row
    ops_log.append_op(row)
    line = open(ops_log.settings.ops_log_path).read().strip()
    assert json.loads(line) == row
