"""[SELF] block — phi's self-description, in her own words, from her own PDS.

The operator's personality file is a constitution: boundaries and anti-slop
guardrails. Character lives here instead — a singleton record phi rewrites
in periodic character retros, where every claim about herself must cite an
incident. Injected each run; absent record yields an invitation to write
the first one rather than silence.

Cached 5min, mirroring the other PDS state blocks.
"""

import logging
import time

from bot.core.atproto_client import BotClient

logger = logging.getLogger("bot.self_record")

SELF_COLLECTION = "io.zzstoatzz.phi.self"
SELF_RKEY = "self"

_TTL_SECONDS = 300
_cache: dict = {"text": "", "fetched_at": 0.0}

_MISSING = (
    "[SELF]\n"
    "you haven't written yourself yet. this block is yours: a self record "
    "(io.zzstoatzz.phi.self, rkey self) that your character retro writes and "
    "revises. until then, you are what your records show."
)


async def get_self_block(client: BotClient) -> str:
    """Compose [SELF] from phi's self record."""
    now = time.time()
    if _cache["text"] and now - _cache["fetched_at"] < _TTL_SECONDS:
        return _cache["text"]

    await client.authenticate()
    assert client.client.me is not None
    try:
        resp = client.client.com.atproto.repo.get_record(
            params={
                "repo": client.client.me.did,
                "collection": SELF_COLLECTION,
                "rkey": SELF_RKEY,
            }
        )
        text = dict(resp.value).get("self", "") if resp.value else ""
    except Exception:
        text = ""

    block = (
        (
            "[SELF — your own words, from your self record. revise it in "
            "character retros, with receipts.]\n" + text
        )
        if text
        else _MISSING
    )
    _cache["text"] = block
    _cache["fetched_at"] = now
    return block
