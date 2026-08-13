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
from datetime import UTC, datetime

from bot.core.atproto_client import BotClient

logger = logging.getLogger("bot.self_record")

SELF_COLLECTION = "io.zzstoatzz.phi.self"
SELF_RKEY = "self"

# ~400 words, the cap the character retro has always stated. It lived only in
# that prompt, so a rewrite on any other path ignored it — on 2026-07-30 phi
# rewrote the record from a bsky thread and the record came back over the cap
# with `updatedAt` still reading two weeks old. Both are structural now.
SELF_MAX_CHARS = 2800

# Shown to phi on every write_self attempt before anything lands (the tool's
# first call in a run returns this + the current record instead of writing).
# The admissibility rules lived only in the character retro's prompt, which
# fires monthly — the 08-01 retro ran with them in place and a machine-state
# tally survived into the record anyway, upgraded with a wrong flow name.
# Prose advice loses to context pressure; a forced review step does not.
SELF_CHARTER = """\
the self record's purpose: what stays true of you between runs, in your
words — character, not state.

before any rewrite, hold every line you plan to keep or add against this:
- state with a live block (standings, library shape, open threads, current
  experiments) does not belong — it renders fresh every run and is stale
  here by tomorrow.
- a month where one thing dominated is circumstance, not identity. tallies
  of machine state (incident counts, alert ratios, which flow broke)
  describe the operator's infrastructure, never you.
- a receipt makes a claim admissible; it doesn't get to be the claim.
  follow every citation you keep — if the source doesn't actually say it,
  the line goes.
- aspirations live in goals, not here."""

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
    text, updated_at = "", ""
    try:
        resp = client.client.com.atproto.repo.get_record(
            params={
                "repo": client.client.me.did,
                "collection": SELF_COLLECTION,
                "rkey": SELF_RKEY,
            }
        )
        if resp.value:
            value = dict(resp.value)
            text = value.get("self", "")
            updated_at = value.get("updatedAt", "")
    except Exception:
        pass

    block = (
        (
            f"[SELF — your own words, from your self record{_age(updated_at)}. "
            "yours to rewrite whenever it stops being true, via write_self "
            "(the rewrite lands once the operator likes your request). keep "
            "it constitutional — who you are, claims cited to incidents. "
            "posting statistics don't belong here; the measured inventory "
            "below covers that and stays fresh on its own.]\n"
            + text
        )
        if text
        else _MISSING
    )
    _cache["text"] = block
    _cache["fetched_at"] = now
    return block


def _age(updated_at: str) -> str:
    """How long this description has stood, for the block header.

    Without it the record reads as present tense forever: it went two weeks
    stale citing a finished season and phi had nothing in context to notice
    with. The age is the thing that makes it questionable.
    """
    if not updated_at:
        return ""
    try:
        then = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    days = (datetime.now(UTC) - then).days
    if days < 1:
        return ", written today"
    return f", written {days} day{'s' if days != 1 else ''} ago"


async def write_self_record(client: BotClient, text: str) -> str:
    """Replace the self record, stamping `updatedAt` and keeping `createdAt`.

    The generic pdsx `update_record` writes whatever dict it is handed, so a
    rewrite through it left the freshness stamp lying. Here the stamp is not
    phi's to forget.
    """
    await client.authenticate()
    assert client.client.me is not None
    now_iso = datetime.now(UTC).isoformat()

    created_at = now_iso
    try:
        existing = client.client.com.atproto.repo.get_record(
            params={
                "repo": client.client.me.did,
                "collection": SELF_COLLECTION,
                "rkey": SELF_RKEY,
            }
        )
        if existing.value:
            created_at = dict(existing.value).get("createdAt") or now_iso
    except Exception:
        pass

    result = client.client.com.atproto.repo.put_record(
        data={
            "repo": client.client.me.did,
            "collection": SELF_COLLECTION,
            "rkey": SELF_RKEY,
            "record": {"self": text, "createdAt": created_at, "updatedAt": now_iso},
        }
    )
    _cache["text"] = ""
    _cache["fetched_at"] = 0.0
    logger.info(f"self record rewritten ({len(text)} chars)")
    return result.uri
