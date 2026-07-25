"""The guard covers every MCP server, not just pdsx.

2026-07-25. Three holes closed at once:

- `delete_record` was absent from pdsx's mutation set, so a delete into
  any collection — including `app.bsky.feed.post` — passed untouched. The
  one destructive verb was the unchecked one.
- semble writes were logged but never override-gated, so safe mode
  stopped phi posting to bluesky while leaving her free to publish cosmik
  cards.
- tangled had no hook at all, and it carries phi's PDS credentials:
  issues and comments there are public actions in her own name.

The operator override lived only in `tools/posting.py` and
`tools/topchicken.py`. Anything reaching the network through an MCP
server went around it.
"""

import pytest

from bot.core import mcp_guard


@pytest.fixture
def calls():
    return []


def call_tool_stub(calls: list):
    async def call_tool(name, args, _):
        calls.append((name, args))
        return "ok"

    return call_tool


def override(active: bool, message: str = "paused while i debug"):
    async def get_override():
        return {"active": active, "message": message, "updatedAt": ""}

    return get_override


# --- the destructive verb that was never checked ---------------------------


async def test_delete_into_a_feed_collection_is_refused(monkeypatch, calls):
    monkeypatch.setattr(mcp_guard, "get_override", override(False))
    guard = mcp_guard.make_mcp_guard("pdsx", "test")
    result = await guard(
        None,
        call_tool_stub(calls),
        "delete_record",
        {"collection": "app.bsky.feed.post", "rkey": "abc"},
    )
    assert "refused" in result
    assert calls == [], "the delete reached pdsx"


async def test_create_and_update_into_a_feed_collection_still_refused(
    monkeypatch, calls
):
    monkeypatch.setattr(mcp_guard, "get_override", override(False))
    guard = mcp_guard.make_mcp_guard("pdsx", "test")
    for verb in ("create_record", "update_record"):
        result = await guard(
            None, call_tool_stub(calls), verb, {"collection": "app.bsky.feed.like"}
        )
        assert "refused" in result
    assert calls == []


async def test_phis_own_collections_still_pass(monkeypatch, calls):
    """The guard must not become a wall — her custom lexicons are the
    point of having pdsx at all."""
    monkeypatch.setattr(mcp_guard, "get_override", override(False))
    guard = mcp_guard.make_mcp_guard("pdsx", "test")
    result = await guard(
        None,
        call_tool_stub(calls),
        "create_record",
        {"collection": "network.cosmik.card"},
    )
    assert result == "ok"
    assert len(calls) == 1


# --- the override now reaches every server ---------------------------------


async def test_override_blocks_a_semble_write(monkeypatch, calls):
    monkeypatch.setattr(mcp_guard, "get_override", override(True))
    guard = mcp_guard.make_mcp_guard("semble", "test")
    result = await guard(
        None,
        call_tool_stub(calls),
        "semble_execute",
        {"code": 'await call_tool("cards_add_url", {"url": "x"})'},
    )
    assert "operator override is active" in result
    assert calls == []


async def test_override_blocks_a_tangled_write(monkeypatch, calls):
    """The gap: issues and comments are public actions in phi's name."""
    monkeypatch.setattr(mcp_guard, "get_override", override(True))
    guard = mcp_guard.make_mcp_guard("tangled", "test")
    result = await guard(
        None, call_tool_stub(calls), "tangled_create_issue", {"title": "x"}
    )
    assert "operator override is active" in result
    assert calls == []


async def test_override_never_blocks_reads(monkeypatch, calls):
    """Safe mode stops phi acting, not thinking — she can still read while
    the operator sorts something out."""
    monkeypatch.setattr(mcp_guard, "get_override", override(True))
    for server, tool in [
        ("pdsx", "get_record"),
        ("pdsx", "list_records"),
        ("tangled", "tangled_get_repo"),
        ("semble", "semble_search"),
        ("pub-search", "pub_search"),
    ]:
        guard = mcp_guard.make_mcp_guard(server, "test")
        assert await guard(None, call_tool_stub(calls), tool, {}) == "ok", tool


async def test_an_unknown_verb_counts_as_a_mutation(monkeypatch, calls):
    """Deny-by-default: over-gating a read costs a retry, under-gating a
    write costs a public action the operator asked not to happen."""
    monkeypatch.setattr(mcp_guard, "get_override", override(True))
    guard = mcp_guard.make_mcp_guard("tangled", "test")
    result = await guard(None, call_tool_stub(calls), "tangled_frobnicate", {})
    assert "operator override is active" in result
    assert calls == []


async def test_mutations_pass_when_no_override_is_active(monkeypatch, calls):
    monkeypatch.setattr(mcp_guard, "get_override", override(False))
    guard = mcp_guard.make_mcp_guard("tangled", "test")
    assert await guard(None, call_tool_stub(calls), "tangled_create_issue", {}) == "ok"
    assert len(calls) == 1
