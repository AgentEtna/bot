"""The semble write logger observes library writes without blocking them.

Provenance for phi's public library is queryable in logfire (run label +
executed code per write) instead of reconstructed from PDS diffs. The
logger must never refuse a call — live authoring is the primary path.
"""

from unittest.mock import AsyncMock, patch

from bot.core.mcp_guard import _semble_writes, make_semble_write_logger


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


async def test_logger_passes_call_through_and_logs():
    call_tool = AsyncMock(return_value="ok")
    process = make_semble_write_logger("batch")
    code = "cards_add_url(url='https://example.com', note='from a conversation')"
    with patch("bot.core.mcp_guard.logfire") as mock_logfire:
        result = await process(None, call_tool, "execute", {"code": code})
    assert result == "ok"
    call_tool.assert_awaited_once_with("execute", {"code": code}, None)
    kwargs = mock_logfire.info.call_args.kwargs
    assert kwargs["run_label"] == "batch"
    assert kwargs["writes"] == ["cards_add_url"]


async def test_logger_silent_on_read_only_execute():
    call_tool = AsyncMock(return_value="ok")
    process = make_semble_write_logger("cycle")
    with patch("bot.core.mcp_guard.logfire") as mock_logfire:
        result = await process(
            None, call_tool, "execute", {"code": "cards_search(query='x')"}
        )
    assert result == "ok"
    mock_logfire.info.assert_not_called()


async def test_logger_ignores_non_execute_tools():
    call_tool = AsyncMock(return_value="schema")
    process = make_semble_write_logger("batch")
    with patch("bot.core.mcp_guard.logfire") as mock_logfire:
        result = await process(None, call_tool, "get_schema", {"name": "cards_add_url"})
    assert result == "schema"
    mock_logfire.info.assert_not_called()
