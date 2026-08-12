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

from unittest.mock import AsyncMock, patch

import pytest

from bot.core import mcp_guard
from bot.core.mcp_guard import _semble_writes, make_mcp_guard


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


# --- rejected arguments are not an outage -----------------------------------
#
# 2026-07-25: phi called collections_update(access_type="open"). semble
# replied `Input should be 'OPEN' or 'CLOSED'` — everything she needed to fix
# it. The wrapper relabelled that as "semble is unavailable right now, skip
# library writes this run", discarding the correction and telling her to give
# up on a typo. She retried anyway and hit a real server-side failure, but the
# first one was hers to fix.


def test_a_validation_error_is_reported_as_correctable():
    from bot.core.mcp_guard import _is_correctable

    assert _is_correctable(
        "1 validation error for call[update]\naccess_type\n  "
        "Input should be 'OPEN' or 'CLOSED' [type=literal_error]"
    )


def test_an_opaque_validation_error_is_still_correctable():
    """Even without the field detail, a rejected argument is not an outage."""
    from bot.core.mcp_guard import _is_correctable

    assert _is_correctable("Error calling tool 'collections_update': Validation error")


def test_transport_failures_are_still_outages():
    """The original degradation still has to work — a genuinely unreachable
    semble must not send phi into a retry loop."""
    from bot.core.mcp_guard import _is_correctable

    for outage in (
        "Connection refused",
        "ReadTimeout: timed out",
        "502 Bad Gateway",
        "ClientConnectorError",
    ):
        assert not _is_correctable(outage), outage


# --- semble: writes are detected inside code-mode, not from the tool name ---


def test_write_detection_ignores_reads():
    code = (
        "results = cards_search(query='gardens')\n"
        "profile = actors_get_profile(identifier='did:plc:x')\n"
        "cols = collections_list()\n"
    )
    assert _semble_writes(code) == []


def test_write_detection_catches_authoring_and_curation():
    code = (
        "card = cards_add_url(url='https://example.com', note='why')\n"
        "connections_create(from_id=card['id'], to_id='y', type='SUPPORTS')\n"
        "cards_remove_from_library(card_id='z')\n"
    )
    assert _semble_writes(code) == [
        "cards_add_url",
        "cards_remove_from_library",
        "connections_create",
    ]


async def test_logger_passes_call_through_and_logs(monkeypatch):
    monkeypatch.setattr(mcp_guard, "get_override", override(False))
    call_tool = AsyncMock(return_value="ok")
    process = make_mcp_guard("semble", "batch")
    code = "cards_add_url(url='https://example.com', note='from a conversation')"
    with patch("bot.core.mcp_guard.logfire") as mock_logfire:
        result = await process(None, call_tool, "execute", {"code": code})
    assert result == "ok"
    call_tool.assert_awaited_once_with("execute", {"code": code}, None)
    kwargs = mock_logfire.info.call_args.kwargs
    assert kwargs["run_label"] == "batch"
    # renamed from `writes` when the hook generalized to every MCP server
    # (2026-07-25); the span is now "{server} mutation during {run_label}".
    assert kwargs["server"] == "semble"
    assert kwargs["changes"] == ["cards_add_url"]


async def test_logger_silent_on_read_only_execute(monkeypatch):
    monkeypatch.setattr(mcp_guard, "get_override", override(False))
    call_tool = AsyncMock(return_value="ok")
    process = make_mcp_guard("semble", "cycle")
    with patch("bot.core.mcp_guard.logfire") as mock_logfire:
        result = await process(
            None, call_tool, "execute", {"code": "cards_search(query='x')"}
        )
    assert result == "ok"
    mock_logfire.info.assert_not_called()


async def test_logger_ignores_non_execute_tools(monkeypatch):
    monkeypatch.setattr(mcp_guard, "get_override", override(False))
    call_tool = AsyncMock(return_value="schema")
    process = make_mcp_guard("semble", "batch")
    with patch("bot.core.mcp_guard.logfire") as mock_logfire:
        result = await process(None, call_tool, "get_schema", {"name": "cards_add_url"})
    assert result == "schema"
    mock_logfire.info.assert_not_called()


# --- the self record: gated by a tool, so the raw verb must not work -------


async def test_raw_writes_to_the_self_record_are_refused(monkeypatch, calls):
    """2026-07-30: phi rewrote her self record twice in one day through
    `update_record` — over the word cap, with `updatedAt` left reading two
    weeks old. The record is owner-gated through write_self; without this
    refusal the gate is decorative."""
    monkeypatch.setattr(mcp_guard, "get_override", override(False))
    guard = mcp_guard.make_mcp_guard("pdsx", "test")
    for verb in ("create_record", "update_record", "delete_record"):
        result = await guard(
            None,
            call_tool_stub(calls),
            verb,
            {"collection": "io.zzstoatzz.phi.self", "rkey": "self"},
        )
        assert "refused" in result
        assert "write_self" in result, "the refusal must name the trusted path"
    assert calls == [], "a raw self-record write reached pdsx"


async def test_the_self_record_is_still_readable_through_pdsx(monkeypatch, calls):
    monkeypatch.setattr(mcp_guard, "get_override", override(False))
    guard = mcp_guard.make_mcp_guard("pdsx", "test")
    result = await guard(
        None,
        call_tool_stub(calls),
        "get_record",
        {"collection": "io.zzstoatzz.phi.self", "rkey": "self"},
    )
    assert result == "ok"
    assert len(calls) == 1


# --- prior coverage rides along on every sizeable MCP read ------------------


async def test_guard_appends_coverage_to_sizeable_read(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    monkeypatch.setattr(mcp_guard, "get_override", override(False))
    monkeypatch.setattr(
        mcp_guard, "coverage_note", AsyncMock(return_value="[PRIOR COVERAGE] note")
    )
    guard = mcp_guard.make_mcp_guard("pdsx", "test")
    big = "x" * 500

    async def call_tool(name, args, meta):
        return big

    ctx = SimpleNamespace(deps=SimpleNamespace(memory=object()))
    result = await guard(ctx, call_tool, "list_records", {"collection": "fm.plyr.track"})
    assert result == f"{big}\n\n[PRIOR COVERAGE] note"


async def test_guard_skips_coverage_for_small_or_memoryless(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    monkeypatch.setattr(mcp_guard, "get_override", override(False))
    note = AsyncMock(return_value="[PRIOR COVERAGE] note")
    monkeypatch.setattr(mcp_guard, "coverage_note", note)
    guard = mcp_guard.make_mcp_guard("pdsx", "test")

    async def call_tool(name, args, meta):
        return "tiny"

    ctx = SimpleNamespace(deps=SimpleNamespace(memory=object()))
    assert await guard(ctx, call_tool, "list_records", {}) == "tiny"

    async def call_tool_big(name, args, meta):
        return "y" * 500

    assert await guard(None, call_tool_big, "list_records", {}) == "y" * 500
    note.assert_not_awaited()
