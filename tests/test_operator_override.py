"""Tests for the operator override (safe mode) and the pdsx feed-write guard.

The override is an io.zzstoatzz.phi.override record on the OPERATOR's repo;
repo ownership is the authorization. While active, outward tools refuse
with the operator's message and the system prompt carries the banner.
"""

from unittest.mock import AsyncMock, patch

import pytest

from bot.core import override as override_mod
from bot.core.mcp_guard import guard_pdsx_tool_call
from bot.core.override import (
    Override,
    get_override_block,
    refusal_text,
)


@pytest.fixture(autouse=True)
def _reset_override_cache():
    override_mod._cache = {"override": None, "fetched_at": 0.0}
    override_mod._pds_cache = None
    yield


def _active(message: str = "we need to talk before you act publicly.") -> Override:
    return {"active": True, "message": message}


# --- refusal + banner text ---


def test_refusal_carries_operator_message_verbatim():
    text = refusal_text(_active("my exact words"))
    assert "my exact words" in text
    assert "was not performed" in text
    assert "devlog" in text  # the channel guidance


async def test_banner_block_renders_when_active():
    with patch.object(override_mod, "get_override", AsyncMock(return_value=_active())):
        block = await get_override_block()
    assert block.startswith("[OPERATOR OVERRIDE]")
    assert "we need to talk" in block
    assert "not a malfunction" in block


async def test_banner_block_empty_when_inactive():
    with patch.object(
        override_mod,
        "get_override",
        AsyncMock(return_value={"active": False, "message": ""}),
    ):
        assert await get_override_block() == ""


# --- reader semantics ---


async def test_fetch_failure_holds_last_known_state():
    override_mod._cache = {"override": _active(), "fetched_at": -9999.0}
    with patch.object(
        override_mod, "_resolve_operator_pds", AsyncMock(return_value=None)
    ):
        result = await override_mod.get_override()
    assert result["active"] is True  # stale-but-held, not flapped to off


async def test_never_fetched_defaults_inactive():
    with patch.object(
        override_mod, "_resolve_operator_pds", AsyncMock(return_value=None)
    ):
        result = await override_mod.get_override()
    assert result["active"] is False


# --- posting tools refuse while active ---


async def test_post_refuses_under_override():
    from pydantic_ai import RunContext

    from bot.tools import posting
    from bot.tools._helpers import PhiDeps

    captured = {}

    class FakeAgent:
        def tool(self, fn):
            captured[fn.__name__] = fn
            return fn

    posting.register(FakeAgent())
    ctx = type("Ctx", (), {"deps": PhiDeps(author_handle="someone")})()

    with (
        patch.object(posting, "get_override", AsyncMock(return_value=_active())),
        patch.object(posting, "check_action") as judge,
        patch.object(posting.bot_client, "create_post", AsyncMock()) as create,
    ):
        result = await captured["post"](ctx, "hello world")

    assert "operator override is active" in result
    judge.assert_not_called()  # override outranks the judge
    create.assert_not_called()

    _ = RunContext  # silence unused-import lint in minimal test env


# --- pdsx guard ---


async def test_guard_blocks_feed_post_create():
    call_tool = AsyncMock()
    result = await guard_pdsx_tool_call(
        None,
        call_tool,
        "create_record",
        {"collection": "app.bsky.feed.post", "record": {"text": "hi", "reply": {}}},
    )
    assert "refused" in result
    assert "post / like_post / repost_post" in result
    call_tool.assert_not_called()


async def test_guard_passes_non_feed_collections():
    call_tool = AsyncMock(return_value={"uri": "at://..."})
    result = await guard_pdsx_tool_call(
        None,
        call_tool,
        "create_record",
        {"collection": "network.cosmik.card", "record": {"kind": "NOTE"}},
    )
    assert result == {"uri": "at://..."}
    call_tool.assert_awaited_once()


async def test_guard_passes_reads():
    call_tool = AsyncMock(return_value={"records": []})
    await guard_pdsx_tool_call(
        None, call_tool, "list_records", {"collection": "app.bsky.feed.post"}
    )
    call_tool.assert_awaited_once()
