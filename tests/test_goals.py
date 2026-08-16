"""update_goal_progress skips the write when nothing changed.

2026-08-15 audit: 99 progress writes in 14 days for 2 goals — the goal
record was being used as a per-run journal (residue's disease, on the
surface that survived it). Unchanged state must not write.
"""

from unittest.mock import AsyncMock, Mock, patch

from bot.core import goals


def _client_with(existing: dict):
    client = Mock()
    client.authenticate = AsyncMock()
    client.client.me.did = "did:plc:test"
    return client


async def test_unchanged_state_returns_none_without_writing():
    client = _client_with({})
    existing = {"current_state": "same", "next_step": "same next", "title": "t"}
    with patch.object(goals, "get_goal", AsyncMock(return_value=existing)):
        result = await goals.update_goal_progress(
            client, "3g", current_state="same", next_step="same next", last_step="x"
        )
    assert result is None
    client.client.com.atproto.repo.put_record.assert_not_called()


async def test_changed_state_writes():
    client = _client_with({})
    client.client.com.atproto.repo.put_record.return_value = Mock(uri="at://g/3g")
    existing = {"current_state": "old", "next_step": "old next", "title": "t"}
    with patch.object(goals, "get_goal", AsyncMock(return_value=existing)):
        result = await goals.update_goal_progress(
            client, "3g", current_state="new", next_step="old next", last_step="x"
        )
    assert result == "at://g/3g"
    client.client.com.atproto.repo.put_record.assert_called_once()
