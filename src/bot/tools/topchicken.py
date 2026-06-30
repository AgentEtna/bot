"""Top Chicken market tool — read the daily Bluesky like-race betting market and
relay a recommendation.

The market (https://topchicken.cee.wtf) is play-money, winner-take-all: a share
pays 100¢ if that account is the day's Top Chicken, else 0¢. bisk.social computes
the strategy server-side and exposes it at /chicken/recommend (see the bisk repo's
functions/_strategy.js). We just fetch and relay — no auth, read-only, and phi
cannot place trades (the market's OAuth is captcha-gated; not automatable).
"""

import logging

import httpx
from pydantic_ai import RunContext

from bot.tools._helpers import PhiDeps

logger = logging.getLogger("bot.tools")

RECOMMEND_URL = "https://bisk.social/top/recommend"


def register(agent):
    @agent.tool
    async def check_chicken_market(
        ctx: RunContext[PhiDeps], handle: str | None = None
    ) -> str:
        """Check the Top Chicken betting market and get a strategy recommendation.

        Top Chicken is a play-money market on who'll win Bluesky's daily most-liked-post
        crown (locks 12:00 UTC, settles ~13:05 UTC). Use this when someone asks what's
        happening in the chicken market or what they should buy.

        Pass `handle` to fold in that player's PUBLIC stats (cash, amount staked, ROI) —
        e.g. the handle of whoever is asking for advice. Their specific positions (which
        chickens they hold) are private and not visible; say so if asked for sell advice.
        """
        params = {}
        if handle:
            params["handle"] = handle.lstrip("@")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(RECOMMEND_URL, params=params)
                r.raise_for_status()
                data = r.json()
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
