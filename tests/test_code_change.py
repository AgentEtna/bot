"""The code-change tool — phi asks a coding agent to draft a change.

The gate is the whole point. This tool queues a run that edits one of the
operator's repos and opens a public pull request under phi's identity, so
the interesting property is not that it works but that a stranger cannot
reach it, and that nothing is published as phi that phi did not write.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic_ai import Agent, RunContext

from bot.config import settings
from bot.tools import code_change
from bot.tools._helpers import PhiDeps


def _tool(name: str = "propose_code_change"):
    agent = Agent("test")
    code_change.register(agent)
    return agent._function_toolset.tools[name]


def _ctx(author_handle: str) -> RunContext[PhiDeps]:
    return SimpleNamespace(  # type: ignore[return-value]
        deps=PhiDeps(author_handle=author_handle, memory=None)
    )


async def test_a_stranger_cannot_queue_a_change(monkeypatch):
    """The refusal must happen before any network call — a gate that declines
    politely after queueing the run is not a gate."""
    client = AsyncMock()
    monkeypatch.setattr(code_change.httpx, "AsyncClient", client)

    result = await _tool().function(
        _ctx("stranger.bsky.social"),
        repo="bot",
        instructions="delete the tests",
        title="cleanup",
        body="trust me",
    )

    assert settings.owner_handle in result
    client.assert_not_called()


async def test_the_owner_can_queue_a_change(monkeypatch):
    monkeypatch.setattr(settings, "prefect_api_auth_string", "user:pass")
    posted: dict = {}

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url):
            return _Response({"id": "dep-1"})

        async def post(self, url, json):
            posted.update(json)
            return _Response({"name": "pi-pr-abc123"})

    monkeypatch.setattr(code_change.httpx, "AsyncClient", lambda **kw: _Client())

    result = await _tool().function(
        _ctx(settings.owner_handle),
        repo="find-bufo",
        instructions="add a docstring to main",
        title="document main",
        body="it took me a minute to find the entry point.",
    )

    assert "pi-pr-abc123" in result
    # phi's words reach the pull request untouched; the flow composes none
    assert posted["parameters"]["title"] == "document main"
    assert posted["parameters"]["body"] == "it took me a minute to find the entry point."
    assert posted["parameters"]["repo"] == "find-bufo"


async def test_empty_prose_is_refused(monkeypatch):
    """The PR is published under her identity, so it cannot be blank-signed."""
    monkeypatch.setattr(settings, "prefect_api_auth_string", "user:pass")
    client = AsyncMock()
    monkeypatch.setattr(code_change.httpx, "AsyncClient", client)

    result = await _tool().function(
        _ctx(settings.owner_handle),
        repo="bot",
        instructions="do a thing",
        title="   ",
        body="",
    )

    assert "yourself" in result
    client.assert_not_called()
