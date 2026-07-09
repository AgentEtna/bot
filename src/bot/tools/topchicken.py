"""Top Chicken market tools — read and trade the daily Bluesky like-race market.

"Top Chicken" is first and foremost a community game, not this market. It's a
daily ranking run by @topchicken.bsky.social (managed by @dave.9000ish.uk), born
from Grace saying "gm top chickens" in 2024: the field is the simcluster of
people dave follows plus his followers, contenders must have under 7k followers
(the "Grace Limit"), and the crown goes to the most-liked post of the day.
bisk.social is a sibling stats site for the same cluster; the prediction market
is a further derivative built on top. Don't conflate the game with the market.

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
LEADERBOARD_URL = "https://topchicken.cee.wtf/api/leaderboard"
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

        "Top Chicken" is a community game — the daily most-liked-post crown among the
        simcluster around @dave.9000ish.uk (his follows + followers, under-7k accounts),
        announced by @topchicken.bsky.social. This tool checks the play-money prediction
        market built ON TOP of that game. If someone asks how to "top chicken", they may
        mean how to WIN the crown (post something the cluster loves) rather than how to
        bet on it — read the intent before reaching for market mechanics. A round is named for a UTC calendar day of posts
        but trades the day AFTER:
        round D opens at D 06:00 UTC, runs through the overnight like-race (much of the
        liking lands overnight, so prices can move a lot after your evening), locks at
        D+1 06:00 UTC, and settles ~D+1 13:05 UTC when @topchicken announces the winner
        (likes counted at D+1 13:00 — so the final ~7h of the race happen after trading
        locks; you can't react to them, price that in before the lock).
        So the posts on the board are about a day old while the race is still live —
        that's normal, not a stale board. Use this when someone asks what's happening
        in the chicken market, what they should buy — or before placing your own trade
        with place_chicken_trade.

        Pass `handle` to fold in that player's PUBLIC stats (cash, amount staked, ROI) —
        e.g. the handle of whoever is asking for advice. All wallets and positions are
        public; use check_chicken_leaderboard to see the season standings and what the
        players ahead of you are holding.
        """
        try:
            market = await _get_json(MARKET_URL)
        except Exception as e:
            logger.warning(f"chicken market fetch failed: {e}")
            return "the chicken market is unreachable right now — try again in a bit"

        round_ = market.get("round") or {}
        contenders = round_.get("contenders", [])
        lines = [
            f"round {round_.get('id')} · {round_.get('status')} · {len(contenders)} contenders"
        ]
        if contenders:
            top = ", ".join(
                f"@{c['handle']} {c['likes']}L (p={c.get('p') or 0:.2f}, ask {c['ask_subc'] / 100:.1f}¢)"
                for c in sorted(
                    contenders, key=lambda c: c.get("p") or 0, reverse=True
                )[:5]
            )
            lines.append(f"board (top 5 by win-probability): {top}")

        # bisk's strategy advice is garnish on top of the live board — its tracker
        # can desync (empty board, "@undefined" leader), so only relay it when it
        # agrees with the market about whether there's a field at all
        params = {"handle": handle.lstrip("@")} if handle else {}
        try:
            rec = await _get_json(RECOMMEND_URL, params=params)
            if contenders and not rec.get("board"):
                logger.warning(
                    "bisk recommend board is empty while the market has "
                    f"{len(contenders)} contenders — dropping its advice as stale"
                )
            else:
                lines.extend(rec.get("advice", []))
        except Exception as e:
            logger.warning(f"bisk recommend fetch failed: {e}")

        return "\n".join(lines)

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
    async def check_chicken_leaderboard(ctx: RunContext[PhiDeps]) -> str:
        """Check the season leaderboard: standings, days left, and rivals' open positions.

        The market runs in week-long SEASONS, and a season is a TOURNAMENT, not a
        savings account: what pays is your final RANK, and the podium is remembered
        forever. That changes correct play, especially late in a season:

        - trailing with few rounds left: steady small-edge betting mathematically
          cannot close a big gap. Seek variance — larger positions on cheaper
          longshots. Finishing 4th by a little and 4th by a lot are the same result,
          so the downside of a miss is smaller than it feels.
        - leading: mirror your chasers' positions. If you hold what they hold, the
          gap can't move and the clock wins for you.
        - to PASS someone you must DIVERGE from them — if you both hold the same
          chicken, nothing changes when it hits. That's why rivals' positions
          (public, shown here for the traders around you) are load-bearing
          information, not gossip: fade what they hold, or hold what they missed.

        Use this alongside check_chicken_market when deciding how much round-to-round
        risk actually fits the season situation.
        """
        try:
            board = await _get_json(LEADERBOARD_URL)
        except Exception as e:
            logger.warning(f"chicken leaderboard fetch failed: {e}")
            return "the chicken market is unreachable right now — try again in a bit"

        info = board.get("season_info") or {}
        leaders = board.get("leaders", [])
        lines = [
            f"season {info.get('num')} · day {info.get('day')}/{info.get('total_days')}"
            f" · final round {info.get('end_round')} (settles ~13:00 UTC the day after)"
        ]

        await bot_client.authenticate()
        assert bot_client.client.me is not None
        my_did = bot_client.client.me.did
        my_rank = next(
            (i + 1 for i, ldr in enumerate(leaders) if ldr.get("did") == my_did), None
        )

        shown = leaders[: max(5, my_rank or 0)]
        for i, ldr in enumerate(shown, start=1):
            you = " ← you" if ldr.get("did") == my_did else ""
            bot_tag = " [bot]" if ldr.get("bot") else ""
            lines.append(
                f"{i}. @{ldr['handle']}{bot_tag} · net {_fmt_subc(ldr.get('pnl_subc', 0))}"
                f" · 24h {_fmt_subc(ldr.get('pnl_24h_subc', 0))}"
                f" · {_fmt_subc(ldr.get('open_subc', 0))} in open positions{you}"
            )
        if my_rank and leaders:
            gap = leaders[0].get("pnl_subc", 0) - leaders[my_rank - 1].get(
                "pnl_subc", 0
            )
            lines.append(f"gap to 1st: {_fmt_subc(gap)}")

        rivals = [ldr for ldr in shown if ldr.get("did") != my_did][:4]
        results = await asyncio.gather(
            *(_get_json(TRADER_URL.format(did=ldr["did"])) for ldr in rivals),
            return_exceptions=True,
        )
        for ldr, r in zip(rivals, results):
            if isinstance(r, BaseException):
                continue
            positions = r.get("positions", [])
            if positions:
                held = ", ".join(
                    f"{p['shares']} @{p['handle']} (avg {p['avg_subc'] / 100:.0f}¢, "
                    f"now {p['mark_subc'] / 100:.0f}¢)"
                    for p in positions
                )
            else:
                held = f"no open positions (cash {_fmt_subc(r.get('balance_subc', 0))})"
            lines.append(f"@{ldr['handle']} holds: {held}")

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
            Field(description="buy to back a contender; sell to exit a position"),
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
        are calibrated win-probabilities. Remember trading locks at 06:00 UTC the
        day after the round's named date (likes keep counting until 13:00), with likes
        landing all through the overnight — a "settled-looking" evening board can still
        reshuffle. Check check_chicken_market
        for the board and check_chicken_portfolio for your balance first. Trades execute against the
        house quote (a ~2% slippage cap is applied automatically) and are final —
        this is a real public record on your repo, so trade like someone whose fills
        are on the permanent ledger.

        Bet every round: back whichever contender you actually believe wins, at modest
        size, and hold to settlement. Agreeing with the board's prices is NOT a reason
        to sit out — cash earns nothing here, and participating is the point (index
        investors buy at consensus prices every payday). The ~2% spread is the cost of
        playing, not a reason to abstain. Pass only if you truly have no opinion on
        who wins. Size with the season in mind: check_chicken_leaderboard shows your
        rank, the time left, and what rivals hold — late in a season, trailing badly,
        modest consensus bets can't change your finish.
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
