"""[SELF STATE] rendering — the snake_case record trap.

`[SELF STATE]` rendered empty in 611 consecutive production requests over
a week. The cause was `dict(record.value).get("createdAt")`: a typed
atproto `Record` serializes to snake_case, so the camelCase lookup
returned "" and the block was never appended. Nothing raised — the
block's empty-when-unset contract made a broken read look like absent
data.
"""

from types import SimpleNamespace

from atproto_client.models.app.bsky.graph.follow import Record as FollowRecord

from bot.core.self_state import _last_follow_when


def fake_client(record_value):
    """A BotClient stub whose list_records returns one follow record."""
    records = [SimpleNamespace(value=record_value)] if record_value else []
    inner = SimpleNamespace(
        me=SimpleNamespace(did="did:plc:phi"),
        com=SimpleNamespace(
            atproto=SimpleNamespace(
                repo=SimpleNamespace(
                    list_records=lambda _params: SimpleNamespace(records=records)
                )
            )
        ),
    )

    async def authenticate():
        return None

    return SimpleNamespace(client=inner, authenticate=authenticate)


async def test_reads_created_at_from_a_typed_record():
    """The regression: a real atproto model, not a dict or a DotDict.

    `FollowRecord.created_at` is the snake_case field; the wire name is
    `createdAt`. Reading the wrong one yields "" and drops the block.
    """
    record = FollowRecord(
        created_at="2026-07-21T23:34:07.016516+00:00", subject="did:plc:someone"
    )
    assert await _last_follow_when(fake_client(record)) != ""


async def test_no_follows_renders_nothing():
    """Empty-when-unset still holds — absent data is not an error."""
    assert await _last_follow_when(fake_client(None)) == ""


async def test_unparseable_timestamp_renders_nothing():
    record = FollowRecord(created_at="not a timestamp", subject="did:plc:someone")
    assert await _last_follow_when(fake_client(record)) == ""
