"""Reaction records (likes/reposts) are governed pdsx writes.

like_post/repost_post retired 2026-08-13: a like is a create_record into
app.bsky.feed.like, and everything that made the tools safe was never
verb-specific — subject verification, self-refusal, the policy judge —
so it lives in mcp_guard's _govern_reaction. These tests hold those
invariants at the guard, including the original self-like regression:
engagement notifications put phi's own post URI in the batch, one
confused hop from liking herself.
"""

from unittest.mock import AsyncMock, patch

from bot.config import settings
from bot.core import mcp_guard
from bot.core.mcp_guard import make_mcp_guard
from bot.tools._helpers import PhiDeps

OWN_URI = "at://did:plc:phiphiphi/app.bsky.feed.post/3xyz"
OTHER_URI = "at://did:plc:someoneelse/app.bsky.feed.post/3abc"


def _ctx(notifs=None):
    return type(
        "Ctx", (), {"deps": PhiDeps(author_handle="", notifications_context=notifs)}
    )()


def _like_args(uri):
    return {"collection": "app.bsky.feed.like", "record": {"subject": {"uri": uri}}}


def _inactive():
    return {"active": False, "message": ""}


def _allow():
    return (None, "")


async def test_guard_refuses_own_post_from_engagement_entry():
    notifs = {
        OWN_URI: {
            "uri": OWN_URI,
            "cid": "bafyfake",
            "author_handle": settings.bluesky_handle,
            "post_text": "phi's own post that someone liked",
            "reason": "like",
        }
    }
    call_tool = AsyncMock()
    with patch.object(mcp_guard, "get_override", AsyncMock(return_value=_inactive())):
        result = await make_mcp_guard("pdsx")(
            _ctx(notifs), call_tool, "create_record", _like_args(OWN_URI)
        )
    assert result.startswith("refused")
    call_tool.assert_not_called()


async def test_guard_refuses_own_did_out_of_batch():
    call_tool = AsyncMock()
    with (
        patch.object(mcp_guard, "get_override", AsyncMock(return_value=_inactive())),
        patch(
            "bot.tools.posting._resolve_post_ref",
            AsyncMock(return_value=("bafyfake", OWN_URI, "bafyfake", "", "text")),
        ),
    ):
        from bot.core.atproto_client import bot_client

        me = type("Me", (), {"did": "did:plc:phiphiphi"})()
        with patch.object(bot_client.client, "me", me, create=True):
            result = await make_mcp_guard("pdsx")(
                _ctx(None), call_tool, "create_record", _like_args(OWN_URI)
            )
    assert result.startswith("refused")
    call_tool.assert_not_called()


async def test_guard_completes_and_writes_like_for_other_author():
    """The happy path: phi passes only subject.uri; the guard verifies via
    the batch, judges, and stamps cid + createdAt into the record."""
    notifs = {
        OTHER_URI: {
            "uri": OTHER_URI,
            "cid": "bafyother",
            "author_handle": "bailey.example.com",
            "post_text": "something interesting",
            "reason": "mention",
        }
    }
    call_tool = AsyncMock(return_value="created at://.../app.bsky.feed.like/3k")
    with (
        patch.object(mcp_guard, "get_override", AsyncMock(return_value=_inactive())),
        patch("bot.tools.posting._policy_gate", AsyncMock(return_value=_allow())),
    ):
        result = await make_mcp_guard("pdsx")(
            _ctx(notifs), call_tool, "create_record", _like_args(OTHER_URI)
        )
    assert "created" in result
    sent = call_tool.await_args.args[1]
    assert sent["record"]["subject"] == {"uri": OTHER_URI, "cid": "bafyother"}
    assert sent["record"]["createdAt"]
    assert sent["record"]["$type"] == "app.bsky.feed.like"


async def test_guard_returns_judge_block_to_phi():
    notifs = {
        OTHER_URI: {
            "uri": OTHER_URI,
            "cid": "bafyother",
            "author_handle": "engagement-farm.example.com",
            "post_text": "follow for follow",
            "reason": "like",
        }
    }
    call_tool = AsyncMock()
    with (
        patch.object(mcp_guard, "get_override", AsyncMock(return_value=_inactive())),
        patch(
            "bot.tools.posting._policy_gate",
            AsyncMock(return_value=("blocked by policy 'pile-on': content engine", "")),
        ),
    ):
        result = await make_mcp_guard("pdsx")(
            _ctx(notifs), call_tool, "create_record", _like_args(OTHER_URI)
        )
    assert result.startswith("blocked by policy")
    call_tool.assert_not_called()


async def test_guard_refuses_unresolvable_subject():
    call_tool = AsyncMock()
    with (
        patch.object(mcp_guard, "get_override", AsyncMock(return_value=_inactive())),
        patch("bot.tools.posting._resolve_post_ref", AsyncMock(return_value=None)),
    ):
        result = await make_mcp_guard("pdsx")(
            _ctx(None), call_tool, "create_record", _like_args("at://bogus/uri/here")
        )
    assert result.startswith("refused")
    call_tool.assert_not_called()


async def test_delete_of_reaction_passes_the_guard():
    """Un-liking is her own record and benign — must not be blocked the way
    other app.bsky.feed.* deletes are."""
    call_tool = AsyncMock(return_value="deleted")
    with patch.object(mcp_guard, "get_override", AsyncMock(return_value=_inactive())):
        result = await make_mcp_guard("pdsx")(
            _ctx(None),
            call_tool,
            "delete_record",
            {"collection": "app.bsky.feed.like", "rkey": "3k"},
        )
    assert result == "deleted"
    call_tool.assert_awaited_once()


async def test_update_of_reaction_still_refused():
    call_tool = AsyncMock()
    result = await make_mcp_guard("pdsx")(
        _ctx(None),
        call_tool,
        "update_record",
        {"collection": "app.bsky.feed.like", "rkey": "3k", "record": {}},
    )
    assert "refused" in result
    call_tool.assert_not_called()
