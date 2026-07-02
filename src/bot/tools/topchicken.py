"""Top Chicken market tools — read and trade the daily Bluesky like-race market.

The market (https://topchicken.cee.wtf) is play-money, winner-take-all: a share
pays $1 (10,000 subcents) if that account is the day's Top Chicken, else $0.
Trades are placed by writing a `wtf.cee.topchicken.order` record to phi's own
repo; the market ingests it from the firehose and executes against the house
quote within ~2s. Full agent guide: https://topchicken.cee.wtf/api/agent

bisk.social computes a strategy recommendation server-side at /chicken/recommend
(see the bisk repo's functions/_strategy.js); check_chicken_market relays it.
"""

import asyncio
import logging
import math
from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
from pydantic import Field
from pydantic_ai import RunContext

from bot.core.atproto_client import bot_client
from bot.core.override import get_override, refusal_text
from bot.tools._helpers import PhiDeps

logger = logging.getLogger("bot.tools")

RECOMMEND_URL = "https://bisk.social/top/recommend"
MARKET_URL = "https://topchicken.cee.wtf/api/market"
TRADER_URL = "https://topchicken.cee.wtf/api/trader/{did}"
ORDER_COLLECTION = "wtf.cee.topchicken.order"


def _fmt_subc(subc: int) -> str:
    return f"${subc / 10000:.2f}"


async def _get_json(url: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()


def register(agent):
    @agent.tool
    async def check_chicken_market(
        ctx: RunContext[PhiDeps], handle: str | None = None
    ) -> str:
        """Check the Top Chicken betting market and get a strategy recommendation.

        Top Chicken is a play-money market on who'll win Bluesky's daily most-liked-post
        crown (locks 12:00 UTC, settles ~13:05 UTC). Use this when someone asks what's
        happening in the chicken market, what they should buy — or before placing your
        own trade with place_chicken_trade.

        Pass `handle` to fold in that player's PUBLIC stats (cash, amount staked, ROI) —
        e.g. the handle of whoever is asking for advice. Their specific positions (which
        chickens they hold) are private and not visible; say so if asked for sell advice.
        """
        params = {}
        if handle:
            params["handle"] = handle.lstrip("@")
        try:
            data = await _get_json(RECOMMEND_URL, params=params)
        except Exception as e:
            logger.warning(f"chicken market fetch failed: {e}")
            return "the chicken market is unreachable right now (it runs on a Raspberry Pi 🥧) — try again in a bit"

        lines = list(data.get("advice", []))
        board = data.get("board", [])
        if board:
            top = ", ".join(
                f"@{c['handle']} {c['likes']}L ({c['ask_c']}¢)" for c in board[:5]
            )
            lines.append(f"Board: {top}")
        return "\n".join(lines) if lines else "no market data available"

    @agent.tool
    async def check_chicken_portfolio(ctx: RunContext[PhiDeps]) -> str:
        """Check your own Top Chicken wallet: balance, open positions, recent trades.

        Use this before trading (to know your balance and what you already hold) or
        when reflecting on how your bets went. Everything here is play money.
        """
        await bot_client.authenticate()
        assert bot_client.client.me is not None
        try:
            data = await _get_json(TRADER_URL.format(did=bot_client.client.me.did))
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return (
                    "you don't have a wallet yet — your first trade via "
                    "place_chicken_trade auto-creates one with $1,000 play money"
                )
            raise
        except Exception as e:
            logger.warning(f"chicken trader fetch failed: {e}")
            return "the chicken market is unreachable right now — try again in a bit"

        lines = [f"balance: {_fmt_subc(data.get('balance_subc', 0))}"]
        positions = data.get("positions", [])
        if positions:
            lines.append("positions:")
            for p in positions:
                lines.append(f"  {p}")
        else:
            lines.append("no open positions")
        trades = data.get("trades", [])
        if trades:
            lines.append(f"recent trades (latest {min(5, len(trades))}):")
            for t in trades[:5]:
                lines.append(f"  {t}")
        return "\n".join(lines)

    @agent.tool
    async def place_chicken_trade(
        ctx: RunContext[PhiDeps],
        contender: Annotated[
            str,
            Field(description="the contender's handle or DID, from the market board"),
        ],
        side: Annotated[
            Literal["buy", "sell"],
            Field(
                description="buy shares you think are underpriced; sell to exit a position"
            ),
        ],
        shares: Annotated[
            int,
            Field(
                gt=0,
                description="number of shares (each pays $1 if the contender wins)",
            ),
        ],
    ) -> str:
        """Place a play-money trade on the Top Chicken market.

        A share pays $1 if that contender wins the day's crown, $0 otherwise; prices
        are calibrated win-probabilities. Check check_chicken_market for the board and
        check_chicken_portfolio for your balance first. Trades execute against the
        house quote (a ~2% slippage cap is applied automatically) and are final —
        this is a real public record on your repo, so trade like someone whose fills
        are on the permanent ledger. Keep stakes proportionate: it's a game, not a
        grind — one or two considered trades beat a flurry.
        """
        override = await get_override()
        if override["active"]:
            return refusal_text(override)

        try:
            market = await _get_json(MARKET_URL)
        except Exception as e:
            logger.warning(f"chicken market fetch failed: {e}")
            return "the chicken market is unreachable right now — try again in a bit"

        round_ = market.get("round") or {}
        if round_.get("status") != "open":
            return f"round {round_.get('id')} is {round_.get('status', 'unknown')} — trades are only accepted while a round is open"

        key = contender.lstrip("@")
        match = next(
            (
                c
                for c in round_.get("contenders", [])
                if c.get("did") == key or c.get("handle") == key
            ),
            None,
        )
        if match is None:
            board = ", ".join(f"@{c['handle']}" for c in round_.get("contenders", []))
            return f"@{key} isn't a contender in round {round_['id']}. current board: {board}"

        if side == "buy":
            quote = match["ask_subc"]
            cap = math.ceil(shares * quote * 1.02)
            cost_note = f"max cost {_fmt_subc(cap)}"
        else:
            quote = match["bid_subc"]
            cap = math.floor(shares * quote * 0.98)
            cost_note = f"min proceeds {_fmt_subc(cap)}"

        await bot_client.authenticate()
        assert bot_client.client.me is not None
        did = bot_client.client.me.did
        bot_client.client.com.atproto.repo.create_record(
            data={
                "repo": did,
                "collection": ORDER_COLLECTION,
                "record": {
                    "$type": ORDER_COLLECTION,
                    "round": round_["id"],
                    "contender": match["did"],
                    "side": side,
                    "shares": shares,
                    "capSubc": cap,
                    "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                },
            }
        )

        summary = (
            f"order placed: {side} {shares} share{'s' if shares != 1 else ''} of "
            f"@{match['handle']} at ~{quote / 100:.0f}¢ ({cost_note}, round {round_['id']})"
        )

        await asyncio.sleep(2.5)
        try:
            trader = await _get_json(TRADER_URL.format(did=did))
            balance = _fmt_subc(trader.get("balance_subc", 0))
            return f"{summary}\nfill confirmed — balance now {balance}"
        except Exception:
            return f"{summary}\ncouldn't confirm the fill yet — check_chicken_portfolio in a moment"
