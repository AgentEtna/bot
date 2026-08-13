"""like_post must refuse phi's own posts.

Engagement notifications render as "@someone liked your post [uri]" where
the only bracketed URI is phi's own post — so "acknowledge the like" is
one confused hop from a self-like. Guard fires on the batch author handle
and, for out-of-batch URIs (which resolve with author_handle=""), on her
own DID in the URI.
"""

from unittest.mock import AsyncMock, patch

from bot.config import settings
from bot.tools import posting
from bot.tools._helpers import PhiDeps

OWN_URI = "at://did:plc:phiphiphi/app.bsky.feed.post/3xyz"


def _tools():
    captured = {}

    class FakeAgent:
        def tool(self, fn):
            captured[fn.__name__] = fn
            return fn

    posting.register(FakeAgent())
    return captured


def _ctx(notifs):
    return type(
        "Ctx", (), {"deps": PhiDeps(author_handle="", notifications_context=notifs)}
    )()


def _inactive():
    return {"active": False, "message": ""}


async def test_like_refuses_own_post_from_engagement_entry():
    tools = _tools()
    notifs = {
        OWN_URI: {
            "uri": OWN_URI,
            "cid": "bafyfake",
            "author_handle": settings.bluesky_handle,
            "post_text": "phi's own post that bailey liked",
            "reason": "like",
        }
    }
    with (
        patch.object(posting, "get_override", AsyncMock(return_value=_inactive())),
        patch.object(posting.bot_client, "like_post", AsyncMock()) as like,
    ):
        result = await tools["like_post"](_ctx(notifs), OWN_URI)

    assert result.startswith("refused")
    like.assert_not_called()


async def test_like_refuses_own_did_out_of_batch():
    tools = _tools()
    with (
        patch.object(posting, "get_override", AsyncMock(return_value=_inactive())),
        patch.object(
            posting,
            "_resolve_post_ref",
            AsyncMock(return_value=("bafyfake", OWN_URI, "bafyfake", "", "text")),
        ),
        patch.object(posting.bot_client, "like_post", AsyncMock()) as like,
    ):
        me = type("Me", (), {"did": "did:plc:phiphiphi"})()
        with patch.object(posting.bot_client.client, "me", me, create=True):
            result = await tools["like_post"](_ctx(None), OWN_URI)

    assert result.startswith("refused")
    like.assert_not_called()


async def test_like_still_works_for_other_authors():
    tools = _tools()
    other = "at://did:plc:someoneelse/app.bsky.feed.post/3abc"
    notifs = {
        other: {
            "uri": other,
            "cid": "bafyother",
            "author_handle": "bailey.example.com",
            "post_text": "something interesting",
            "reason": "mention",
        }
    }
    with (
        patch.object(posting, "get_override", AsyncMock(return_value=_inactive())),
        patch.object(posting.bot_client, "like_post", AsyncMock()) as like,
    ):
        result = await tools["like_post"](_ctx(notifs), other)

    assert result.startswith("liked")
    like.assert_awaited_once()
