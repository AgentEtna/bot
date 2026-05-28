"""[RECENT OPERATIONS] — phi's last N record writes on its own PDS.

Action continuity signal — phi can see WHAT it has been doing across
collections (posts, likes, follows, goals, cosmik cards, blog docs)
without enumerating by hand.

Post text is deliberately NOT shown. The block is read every run, and
including phi's own post bodies would turn the continuity signal into
style training data — concrete in-context examples beat abstract rules,
so feeding phi her own recent prose teaches whatever register that prose
happens to be in. Show actions and counts, not voice. Titles for
intentionally-titled artifacts (goals, blog docs, URL cards) are fine —
those are public anchor names, not posting register.

Cached at 5min, mirroring the other PDS state blocks.

Render is split from fetch so a future jinja migration only has to
replace `_render`. `_summarize` carries per-NSID formatting logic.
"""

from __future__ import annotations

import logging
import time
from typing import TypedDict

from bot.core.atproto_client import BotClient
from bot.utils.time import relative_when

logger = logging.getLogger("bot.recent_operations")

# Collections that count as "phi did something." Excluded: profile
# updates, blocks, plyr/2048/ken records, mcp attestation. The list
# is intentional — not every write, just the ones that count as
# actions worth seeing in a continuity feed.
MEANINGFUL_COLLECTIONS: tuple[str, ...] = (
    "app.bsky.feed.post",
    "app.bsky.feed.like",
    "app.bsky.feed.repost",
    "app.bsky.graph.follow",
    "io.zzstoatzz.phi.goal",
    "network.cosmik.card",
    "network.cosmik.connection",
    "app.greengale.document",
)

PER_COLLECTION_LIMIT = 10
TOP_N = 10

_BLOCK_TTL_SECONDS = 300  # 5min, mirrors core/self_state.py
_block_cache: dict = {"text": "", "fetched_at": 0.0}


class _Row(TypedDict):
    rkey: str
    nsid: str
    created_at: str
    summary: str


def _summarize(nsid: str, value: dict) -> str:
    """One-line salient summary of a record value, by NSID.

    Communicates WHAT phi did, not WHAT she said — post text is deliberately
    omitted so this block doesn't double as style training data feeding
    phi's voice back to her.
    """
    if nsid == "app.bsky.feed.post":
        char_count = len(value.get("text", "") or "")
        if value.get("reply"):
            return f"reply ({char_count} chars)"
        return f"top-level post ({char_count} chars)"
    if nsid == "app.bsky.feed.like":
        subject = value.get("subject") or {}
        uri = subject.get("uri", "") if isinstance(subject, dict) else ""
        return f"like → {uri}"
    if nsid == "app.bsky.feed.repost":
        subject = value.get("subject") or {}
        uri = subject.get("uri", "") if isinstance(subject, dict) else ""
        return f"repost → {uri}"
    if nsid == "app.bsky.graph.follow":
        return f"follow → {value.get('subject', '')}"
    if nsid == "io.zzstoatzz.phi.goal":
        title = value.get("title", "untitled")
        created = value.get("created_at", "")
        updated = value.get("updated_at", "")
        verb = "updated" if (updated and created and updated != created) else "created"
        return f"goal {verb}: {title!r}"
    if nsid == "network.cosmik.card":
        kind = (value.get("type") or "").upper()
        if kind == "URL":
            # URL card titles are intentional public anchor names, not
            # posting register — surface the title, not the description.
            content = value.get("content") or {}
            title = ""
            if isinstance(content, dict):
                metadata = content.get("metadata") or {}
                if isinstance(metadata, dict):
                    title = metadata.get("title", "")
                title = title or content.get("title", "")
            return f"URL card: {title!r}" if title else "URL card"
        # NOTE cards are text-bodied — show kind only so the body doesn't
        # become voice training.
        return f"{kind} card" if kind else "card"
    if nsid == "network.cosmik.connection":
        ctype = value.get("connectionType", "")
        src = value.get("source", "")
        tgt = value.get("target", "")
        return f"connection {ctype}: {src.split('/')[-1]} → {tgt.split('/')[-1]}"
    if nsid == "app.greengale.document":
        return f"doc published: {value.get('title', 'untitled')!r}"
    return ""


def _created_at_from(value: dict) -> str:
    """Extract a createdAt-ish timestamp from a record value."""
    for key in ("createdAt", "created_at", "publishedAt"):
        v = value.get(key)
        if v:
            return str(v)
    return ""


def _fetch_collection(client: BotClient, did: str, nsid: str) -> list[_Row]:
    """List records for one collection on phi's repo. Sync-call style matches goals/self_state."""
    try:
        response = client.client.com.atproto.repo.list_records(
            {
                "repo": did,
                "collection": nsid,
                "limit": PER_COLLECTION_LIMIT,
            }
        )
    except Exception as e:
        logger.debug(f"list {nsid} failed: {e}")
        return []

    rows: list[_Row] = []
    for rec in response.records or []:
        value = dict(rec.value) if rec.value else {}
        rkey = rec.uri.split("/")[-1]
        rows.append(
            _Row(
                rkey=rkey,
                nsid=nsid,
                created_at=_created_at_from(value),
                summary=_summarize(nsid, value),
            )
        )
    return rows


def _render(rows: list[_Row]) -> str:
    """Render rows as the [RECENT OPERATIONS] block. Pure function — easy to template later."""
    if not rows:
        return ""
    nsid_width = max(len(r["nsid"]) for r in rows)
    lines = [
        "[RECENT OPERATIONS — your last writes on PDS, chronological. post "
        "text hidden; actions only.]"
    ]
    for r in rows:
        ts = r["created_at"]
        when = relative_when(ts) if ts else ""
        time_part = f"{ts[:19]}Z ({when})" if ts and when else (ts or "")
        nsid_part = r["nsid"].ljust(nsid_width)
        lines.append(f"{time_part}  {nsid_part}  {r['summary']}")
    return "\n".join(lines)


async def get_operations_block(client: BotClient) -> str:
    """Fetch + render the [RECENT OPERATIONS] block. Cached 5min."""
    now = time.time()
    if _block_cache["text"] and now - _block_cache["fetched_at"] < _BLOCK_TTL_SECONDS:
        return _block_cache["text"]

    try:
        await client.authenticate()
    except Exception:
        return ""
    if not client.client.me:
        return ""
    did = client.client.me.did

    all_rows: list[_Row] = []
    for nsid in MEANINGFUL_COLLECTIONS:
        all_rows.extend(_fetch_collection(client, did, nsid))

    # rkeys are TIDs (millisecond-ordered) — descending rkey = newest first.
    all_rows.sort(key=lambda r: r["rkey"], reverse=True)

    block = _render(all_rows[:TOP_N])
    _block_cache["text"] = block
    _block_cache["fetched_at"] = now
    return block
