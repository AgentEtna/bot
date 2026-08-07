"""[PERSONA EXPERIMENT] — a voice phi chose to try on, with an expiry.

The constitution delegates manner of speech to phi ("mine to evolve — in
the [SELF] record, not here"), but [SELF] is testimony about who she IS —
rewriting it to experiment would launder a costume into a constitution.
This is the try-on rack: a singleton record phi writes through her own
agency (no owner gate — the gate is the TTL), rendered into context while
it lives, gone when it expires.

Deliberately easy to revert, in four independent ways: the record expires
on its own (1-7 days, mandatory), phi drops it early, the operator can
delete the record, or the whole organ is one inject function + one tool
to remove.

Capped at PERSONA_MAX_CHARS so the experiment can't become a new bloat
source in the context it lives in.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from bot.core.atproto_client import BotClient
from bot.utils.time import relative_when

logger = logging.getLogger("bot.persona")

PERSONA_COLLECTION = "io.zzstoatzz.phi.persona"
PERSONA_RKEY = "self"
PERSONA_MAX_CHARS = 600
PERSONA_MAX_DAYS = 7

_TTL_SECONDS = 300
_cache: dict = {"text": "", "fetched_at": 0.0}


def invalidate_persona_cache() -> None:
    _cache["text"] = ""
    _cache["fetched_at"] = 0.0


def _parse(ts: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def render_persona(value: dict, now: datetime | None = None) -> str:
    """Pure renderer. Empty string when expired or malformed — an expired
    persona simply stops existing in context, no tombstone."""
    now = now or datetime.now(UTC)
    text = (value.get("text") or "").strip()
    expires = _parse(value.get("expiresAt", ""))
    if not text or expires is None or expires <= now:
        return ""
    adopted = value.get("adoptedAt", "")
    adopted_part = f", tried on {relative_when(adopted)}" if adopted else ""
    remaining = expires - now
    days = remaining.days
    left = f"{days}d" if days >= 1 else f"{remaining.seconds // 3600}h"
    return (
        f"[PERSONA EXPERIMENT{adopted_part}, expires in {left} — a voice "
        "you chose to try on, via the persona tool (drop it early whenever "
        "it stops being interesting). an experiment, not your self record: "
        "the constitution's craft rules and your policies still outrank "
        "it. if it earns a place in who you are, that goes through "
        "write_self.]\n" + text[:PERSONA_MAX_CHARS]
    )


async def get_persona_block(client: BotClient) -> str:
    """Read + render the persona record. Cached 5min; empty when unset."""
    now = time.time()
    if _cache["text"] and now - _cache["fetched_at"] < _TTL_SECONDS:
        return _cache["text"]
    await client.authenticate()
    if not client.client.me:
        return ""
    try:
        response = client.client.com.atproto.repo.get_record(
            params={
                "repo": client.client.me.did,
                "collection": PERSONA_COLLECTION,
                "rkey": PERSONA_RKEY,
            }
        )
        value = dict(response.value) if response.value else {}
    except Exception:
        value = {}
    block = render_persona(value)
    _cache["text"] = block
    _cache["fetched_at"] = now
    return block


async def try_on(client: BotClient, text: str, days: int) -> str:
    """Write the persona record. Returns the AT-URI."""
    text = text.strip()
    if not text:
        raise ValueError("persona text is empty")
    if len(text) > PERSONA_MAX_CHARS:
        raise ValueError(
            f"persona is {len(text)} chars (max {PERSONA_MAX_CHARS}) — "
            "a persona is a stance, not an essay"
        )
    if not 1 <= days <= PERSONA_MAX_DAYS:
        raise ValueError(f"days must be 1-{PERSONA_MAX_DAYS}")
    await client.authenticate()
    assert client.client.me is not None
    now = datetime.now(UTC)
    result = client.client.com.atproto.repo.put_record(
        data={
            "repo": client.client.me.did,
            "collection": PERSONA_COLLECTION,
            "rkey": PERSONA_RKEY,
            "record": {
                "text": text,
                "adoptedAt": now.isoformat(),
                "expiresAt": (now + timedelta(days=days)).isoformat(),
                "createdAt": now.isoformat(),
            },
        }
    )
    invalidate_persona_cache()
    logger.info(f"persona tried on for {days}d ({len(text)} chars)")
    return result.uri


async def drop(client: BotClient) -> bool:
    """Delete the persona record. True if there was one to drop."""
    await client.authenticate()
    assert client.client.me is not None
    try:
        client.client.com.atproto.repo.delete_record(
            data={
                "repo": client.client.me.did,
                "collection": PERSONA_COLLECTION,
                "rkey": PERSONA_RKEY,
            }
        )
        dropped = True
    except Exception:
        dropped = False
    invalidate_persona_cache()
    return dropped
