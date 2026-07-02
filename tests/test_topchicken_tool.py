"""Regression tests for the Top Chicken market tools.

check_chicken_market fetches bisk.social/chicken/recommend and relays its
`advice` lines; place_chicken_trade reads /api/market and writes an order
record to phi's own repo. We stub HTTP + the bot client so nothing hits the
network.
"""

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from bot.tools import topchicken


class _Recorder:
    """Captures the registered tool fns by name so we can call them directly."""

    def __init__(self):
        self.fns = {}

    def tool(self, fn):
        self.fns[fn.__name__] = fn
        return fn


def _register():
    rec = _Recorder()
    topchicken.register(rec)
    return rec.fns


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


OPEN_MARKET = {
    "round": {
        "id": "2026-07-02",
        "status": "open",
        "contenders": [
            {
                "did": "did:plc:goose",
                "handle": "goose.art",
                "likes": 246,
                "p": 0.34,
                "ask_subc": 3468,
                "bid_subc": 3332,
            }
        ],
    }
}


def _patch_get_json(responses):
    """Patch _get_json to answer by URL (market vs bisk recommend)."""

    async def fake(url, params=None):
        result = responses[url]
        if isinstance(result, Exception):
            raise result
        return result

    return patch("bot.tools.topchicken._get_json", side_effect=fake)


async def test_board_comes_from_the_market_with_bisk_advice_as_garnish():
    fn = _register()["check_chicken_market"]
    rec = {
        "advice": ["Mind the 2% spread."],
        "board": [{"handle": "goose.art", "likes": 246, "ask_c": 34.7}],
    }
    with _patch_get_json(
        {topchicken.MARKET_URL: OPEN_MARKET, topchicken.RECOMMEND_URL: rec}
    ):
        out = await fn(SimpleNamespace(), handle="@zzstoatzz.io")

    assert "round 2026-07-02 · open · 1 contenders" in out
    assert "@goose.art 246L (p=0.34, ask 34.7¢)" in out
    assert "Mind the 2% spread" in out


async def test_stale_bisk_advice_is_dropped_when_market_has_contenders():
    """Regression: bisk's tracker desynced (empty board, '0 contenders' advice)
    while the real market had 129 contenders; phi relayed the fiction verbatim."""
    fn = _register()["check_chicken_market"]
    rec = {"advice": ["Wide open with 0 contenders."], "board": []}
    with _patch_get_json(
        {topchicken.MARKET_URL: OPEN_MARKET, topchicken.RECOMMEND_URL: rec}
    ):
        out = await fn(SimpleNamespace())

    assert "0 contenders" not in out
    assert "@goose.art" in out


async def test_market_unreachable_is_handled_gracefully():
    fn = _register()["check_chicken_market"]
    with _patch_get_json({topchicken.MARKET_URL: httpx.ConnectError("boom")}):
        out = await fn(SimpleNamespace())
    assert "unreachable" in out.lower()


async def test_bisk_unreachable_still_returns_the_board():
    fn = _register()["check_chicken_market"]
    with _patch_get_json(
        {
            topchicken.MARKET_URL: OPEN_MARKET,
            topchicken.RECOMMEND_URL: httpx.ConnectError("pi is down"),
        }
    ):
        out = await fn(SimpleNamespace())
    assert "@goose.art" in out


def _mock_bot_client():
    bc = MagicMock()
    bc.authenticate = AsyncMock()
    bc.client.me.did = "did:plc:phi"
    return bc


async def test_trade_refuses_when_round_not_open():
    fn = _register()["place_chicken_trade"]
    market = {"round": {"id": "2026-07-02", "status": "locked", "contenders": []}}
    ctx_mgr, _ = _mock_client(market)
    with (
        patch("bot.tools.topchicken.httpx.AsyncClient", return_value=ctx_mgr),
        patch(
            "bot.tools.topchicken.get_override",
            AsyncMock(return_value={"active": False, "message": ""}),
        ),
    ):
        out = await fn(SimpleNamespace(), contender="goose.art", side="buy", shares=5)
    assert "locked" in out
    assert "only accepted while a round is open" in out


async def test_trade_refuses_unknown_contender():
    fn = _register()["place_chicken_trade"]
    ctx_mgr, _ = _mock_client(OPEN_MARKET)
    with (
        patch("bot.tools.topchicken.httpx.AsyncClient", return_value=ctx_mgr),
        patch(
            "bot.tools.topchicken.get_override",
            AsyncMock(return_value={"active": False, "message": ""}),
        ),
    ):
        out = await fn(
            SimpleNamespace(), contender="@nobody.example", side="buy", shares=5
        )
    assert "isn't a contender" in out
    assert "@goose.art" in out


async def test_buy_writes_order_record_with_slippage_cap():
    fn = _register()["place_chicken_trade"]
    ctx_mgr, _ = _mock_client(OPEN_MARKET)
    bc = _mock_bot_client()
    with (
        patch("bot.tools.topchicken.httpx.AsyncClient", return_value=ctx_mgr),
        patch("bot.tools.topchicken.bot_client", bc),
        patch(
            "bot.tools.topchicken.get_override",
            AsyncMock(return_value={"active": False, "message": ""}),
        ),
        patch("bot.tools.topchicken.asyncio.sleep", AsyncMock()),
    ):
        out = await fn(SimpleNamespace(), contender="@goose.art", side="buy", shares=25)

    data = bc.client.com.atproto.repo.create_record.call_args.kwargs["data"]
    assert data["repo"] == "did:plc:phi"
    assert data["collection"] == "wtf.cee.topchicken.order"
    record = data["record"]
    assert record["round"] == "2026-07-02"
    assert record["contender"] == "did:plc:goose"
    assert record["side"] == "buy"
    assert record["shares"] == 25
    assert record["capSubc"] == math.ceil(25 * 3468 * 1.02)
    assert "order placed" in out


async def test_sell_cap_is_a_floor_on_proceeds():
    fn = _register()["place_chicken_trade"]
    ctx_mgr, _ = _mock_client(OPEN_MARKET)
    bc = _mock_bot_client()
    with (
        patch("bot.tools.topchicken.httpx.AsyncClient", return_value=ctx_mgr),
        patch("bot.tools.topchicken.bot_client", bc),
        patch(
            "bot.tools.topchicken.get_override",
            AsyncMock(return_value={"active": False, "message": ""}),
        ),
        patch("bot.tools.topchicken.asyncio.sleep", AsyncMock()),
    ):
        await fn(SimpleNamespace(), contender="did:plc:goose", side="sell", shares=10)

    record = bc.client.com.atproto.repo.create_record.call_args.kwargs["data"]["record"]
    assert record["side"] == "sell"
    assert record["capSubc"] == math.floor(10 * 3332 * 0.98)


async def test_trade_refuses_under_operator_override():
    fn = _register()["place_chicken_trade"]
    bc = _mock_bot_client()
    override = {"active": True, "message": "paused for maintenance"}
    with (
        patch("bot.tools.topchicken.bot_client", bc),
        patch("bot.tools.topchicken.get_override", AsyncMock(return_value=override)),
    ):
        out = await fn(SimpleNamespace(), contender="goose.art", side="buy", shares=5)
    assert "paused for maintenance" in out
    bc.client.com.atproto.repo.create_record.assert_not_called()
