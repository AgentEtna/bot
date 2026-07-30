"""The self record — phi's own description of herself.

2026-07-30. Two failures in one day, both from the same cause: the record
was writable by raw `update_record`, so nothing enforced its shape.

- it went two weeks stale (citing a finished season and doctrine v1.4 while
  she was on v1.24) and phi had no way to notice — the [SELF] block injected
  the text with the `updatedAt` stripped, so it read as present tense forever.
- rewriting it from a bsky thread produced a body over the ~400-word cap with
  `updatedAt` still reading the original date. The cap lived in the character
  retro's prompt, which fires monthly and had never fired at all.

The record stays phi's to write. What is enforced here is that it is stamped,
bounded, and passed through the owner gate her goals already use.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from pydantic_ai import Agent, RunContext

from bot.config import settings
from bot.core import self_record
from bot.core.self_record import SELF_MAX_CHARS, _age, get_self_block, write_self_record
from bot.tools import self_record as self_record_tool
from bot.tools._helpers import PhiDeps


def _client(value: dict | None):
    """A BotClient stand-in whose get_record returns `value`."""
    repo = MagicMock()
    repo.get_record.return_value = SimpleNamespace(value=value)
    repo.put_record.return_value = SimpleNamespace(
        uri="at://did:plc:phi/io.zzstoatzz.phi.self/self"
    )
    client = MagicMock()
    client.authenticate = AsyncMock()
    client.client.me = SimpleNamespace(did="did:plc:phi")
    client.client.com.atproto.repo = repo
    return client, repo


@pytest.fixture(autouse=True)
def _clear_cache():
    self_record._cache.update({"text": "", "fetched_at": 0.0})
    yield
    self_record._cache.update({"text": "", "fetched_at": 0.0})


# --- the stamp -------------------------------------------------------------


async def test_writing_stamps_updated_at_now():
    client, repo = _client({"self": "old", "createdAt": "2026-07-15T06:46:19Z"})
    await write_self_record(client, "new text")

    written = repo.put_record.call_args.kwargs["data"]["record"]
    assert written["self"] == "new text"
    stamped = datetime.fromisoformat(written["updatedAt"])
    assert (datetime.now(UTC) - stamped).total_seconds() < 60


async def test_writing_preserves_created_at():
    """Drift is the point of the record — when it was first written stays
    true no matter how many times it is rewritten."""
    client, repo = _client({"self": "old", "createdAt": "2026-07-15T06:46:19Z"})
    await write_self_record(client, "new text")
    written = repo.put_record.call_args.kwargs["data"]["record"]
    assert written["createdAt"] == "2026-07-15T06:46:19Z"


async def test_a_first_write_gets_both_stamps():
    client, repo = _client(None)
    await write_self_record(client, "first")
    written = repo.put_record.call_args.kwargs["data"]["record"]
    assert written["createdAt"] and written["updatedAt"]


async def test_writing_invalidates_the_block_cache():
    """The block is cached 5 minutes; a rewrite that keeps serving the old
    text would let phi read a self-description she had just replaced."""
    self_record._cache.update({"text": "[SELF]stale", "fetched_at": 1e12})
    client, _ = _client({"self": "old", "createdAt": "2026-07-15T06:46:19Z"})
    await write_self_record(client, "new")
    assert self_record._cache["text"] == ""


# --- the age, which is what makes staleness noticeable ---------------------


async def test_the_block_says_how_old_the_record_is():
    written = (datetime.now(UTC) - timedelta(days=15)).isoformat()
    client, _ = _client({"self": "i track things", "updatedAt": written})
    block = await get_self_block(client)
    assert "15 days ago" in block
    assert "i track things" in block


async def test_a_record_written_today_says_so():
    client, _ = _client({"self": "x", "updatedAt": datetime.now(UTC).isoformat()})
    assert "written today" in await get_self_block(client)


@pytest.mark.parametrize("stamp", ["", "not-a-date", "2026-13-45"])
def test_an_unusable_stamp_degrades_to_no_age_claim(stamp: str):
    """Better to say nothing about age than to say something false."""
    assert _age(stamp) == ""


async def test_the_block_no_longer_defers_revision_to_the_retro():
    """The old header read 'revise it in character retros', which told phi
    that noticing a stale record mid-run was out of scope — and the retro
    had never once run."""
    client, _ = _client({"self": "x", "updatedAt": datetime.now(UTC).isoformat()})
    block = await get_self_block(client)
    assert "character retro" not in block
    assert "write_self" in block


# --- the gate --------------------------------------------------------------


def _tool(name: str):
    agent = Agent("test")
    self_record_tool.register(agent)
    return agent._function_toolset.tools[name]


def _ctx(author_handle: str) -> RunContext[PhiDeps]:
    return SimpleNamespace(  # type: ignore[return-value]
        deps=PhiDeps(author_handle=author_handle, memory=None)
    )


async def test_a_stranger_cannot_rewrite_who_phi_is(monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(self_record_tool, "write_self_record", called)
    result = await _tool("write_self").function(_ctx("stranger.bsky.social"), "text")
    assert settings.owner_handle in result
    called.assert_not_awaited()


async def test_the_owner_can(monkeypatch):
    monkeypatch.setattr(
        self_record_tool,
        "write_self_record",
        AsyncMock(return_value="at://did:plc:phi/io.zzstoatzz.phi.self/self"),
    )
    result = await _tool("write_self").function(_ctx(settings.owner_handle), "text")
    assert "rewritten" in result


async def test_the_length_cap_is_structural():
    """~400 words as a sentence in a monthly prompt is not a cap. pydantic
    refuses to dispatch the call at all."""
    schema = _tool("write_self").function_schema
    assert schema.json_schema["properties"]["text"]["maxLength"] == SELF_MAX_CHARS
    with pytest.raises(ValidationError):
        schema.validator.validate_python({"text": "x" * (SELF_MAX_CHARS + 1)})
