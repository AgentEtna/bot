"""Regression tests for the Top Chicken market tool.

The tool fetches bisk.social/chicken/recommend and relays its `advice` lines.
We stub the HTTP call so we exercise the relay + the unreachable path without
hitting the network.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.tools import topchicken


class _Recorder:
    """Captures the registered tool fn so we can call it directly."""

    def __init__(self):
        self.fn = None

    def tool(self, fn):
        self.fn = fn
        return fn


def _register():
    rec = _Recorder()
    topchicken.register(rec)
    assert rec.fn is not None
    return rec.fn


def _mock_client(json_payload=None, raise_exc=None):
    """A stand-in async client whose .get returns a response (or raises)."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_payload or {})
    client = MagicMock()
    if raise_exc is not None:
        client.get = AsyncMock(side_effect=raise_exc)
    else:
        client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


@pytest.mark.asyncio
async def test_relays_advice_and_board():
    fn = _register()
    payload = {
        "advice": ["Round 2026-06-30 · early · locks in 16h.", "Mind the 2% spread."],
        "board": [
            {"handle": "eikopf.com", "likes": 114, "ask_c": 2.7},
            {"handle": "philpax.me", "likes": 111, "ask_c": 2.6},
        ],
    }
    ctx_mgr, client = _mock_client(payload)
    with patch("bot.tools.topchicken.httpx.AsyncClient", return_value=ctx_mgr):
        out = await fn(SimpleNamespace(), handle="@zzstoatzz.io")

    assert "Mind the 2% spread" in out
    assert "@eikopf.com 114L (2.7¢)" in out
    # the @ prefix is stripped before querying
    assert client.get.call_args.kwargs["params"]["handle"] == "zzstoatzz.io"


@pytest.mark.asyncio
async def test_unreachable_is_handled_gracefully():
    fn = _register()
    ctx_mgr, _ = _mock_client(raise_exc=httpx.ConnectError("boom"))
    with patch("bot.tools.topchicken.httpx.AsyncClient", return_value=ctx_mgr):
        out = await fn(SimpleNamespace())
    assert "unreachable" in out.lower()
