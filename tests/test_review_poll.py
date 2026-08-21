"""Review comments reach phi even when jetstream does not deliver them.

2026-08-21: three jetstream failures in one afternoon (a quiet socket, an
instance without the event, a cursor past it) each cost a review comment.
The reviewers' PDSes are the authority; the poll reads them, and the
handled set keeps the two paths from waking her twice for one comment.
"""

import json
from unittest.mock import AsyncMock, Mock

import pytest

from bot.core import review_poll

PHI = "did:plc:phi"
OWNER = "did:plc:owner"
PULL = f"at://{PHI}/sh.tangled.repo.pull/3abc"


def _record(uri_rkey, subject, created="2099-01-01T00:00:00Z"):
    return {
        "uri": f"at://{OWNER}/sh.tangled.feed.comment/{uri_rkey}",
        "value": {
            "subject": {"uri": subject, "cid": "bafy"},
            "body": {"text": "2/10"},
            "createdAt": created,
        },
    }


@pytest.fixture
def handled_file(tmp_path, monkeypatch):
    monkeypatch.setattr(review_poll, "HANDLED_FILE", tmp_path / "h.json")
    monkeypatch.setattr(review_poll, "_pds_cache", {OWNER: "https://pds.example"})
    from bot.core import ops_log

    monkeypatch.setattr(ops_log, "WATCHED_CURSOR_FILE", tmp_path / "w.json")
    return tmp_path / "h.json"


def _http(records):
    http = Mock()
    http.get = AsyncMock(return_value=Mock(text=json.dumps({"records": records})))
    return http


async def test_new_comments_on_phis_pulls_only_once(handled_file):
    records = [
        _record("r1", PULL),
        _record("r2", "at://did:plc:other/sh.tangled.repo.pull/x"),
    ]
    found = await review_poll.new_review_comments(PHI, (OWNER,), _http(records))
    assert [c["uri"] for c in found] == [f"at://{OWNER}/sh.tangled.feed.comment/r1"]
    review_poll.mark_handled(found[0]["uri"])
    assert await review_poll.new_review_comments(PHI, (OWNER,), _http(records)) == []


async def test_jetstream_and_poll_share_the_handled_set(handled_file):
    uri = f"at://{OWNER}/sh.tangled.feed.comment/r1"
    review_poll.mark_handled(uri)
    assert review_poll.was_handled(uri)
    assert (
        await review_poll.new_review_comments(
            PHI, (OWNER,), _http([_record("r1", PULL)])
        )
        == []
    )


async def test_old_comments_are_not_replayed_on_a_fresh_start(handled_file):
    found = await review_poll.new_review_comments(
        PHI, (OWNER,), _http([_record("old", PULL, created="2020-01-01T00:00:00Z")])
    )
    assert found == []


async def test_comments_the_jetstream_path_already_handled_are_not_rewoken(handled_file, tmp_path):
    """18:45: the first poll woke phi for the 17:13 comment jetstream had
    handled at 17:42 — recorded in the watched cursor, not the handled set."""
    from datetime import datetime, timezone

    from bot.core import ops_log

    t = datetime(2099, 1, 1, tzinfo=timezone.utc)
    ops_log._set_watched_cursor(int(t.timestamp() * 1_000_000))
    older = _record("r1", PULL, created=t.isoformat().replace("+00:00", "Z"))
    newer = _record("r2", PULL, created="2099-01-02T00:00:00Z")
    found = await review_poll.new_review_comments(PHI, (OWNER,), _http([older, newer]))
    assert [c["uri"].rsplit("/", 1)[-1] for c in found] == ["r2"]
