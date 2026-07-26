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
import re
import time
from typing import TypedDict

from atproto_client.models.utils import get_model_as_dict

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

POST_PREVIEW = 220
"""How much of a top-level post to show. Enough to recognise a subject she
has already covered; not the whole feed re-rendered into her context."""

PER_COLLECTION_LIMIT = 10
TOP_N = 10

_BLOCK_TTL_SECONDS = 300  # 5min, mirrors core/self_state.py
_block_cache: dict = {"text": "", "fetched_at": 0.0}


class _Row(TypedDict):
    rkey: str
    nsid: str
    created_at: str
    summary: str


_URL_RE = re.compile(r"https?://[^\s<>\")\]]+")


def _links_in(value: dict) -> list[str]:
    """URLs phi put in a post, from its facets and its text.

    Facets are authoritative (they carry the real target of a shortened
    display string); the text scan catches posts written before facets were
    resolved. Trimmed to host + path so the line stays short and two posts
    sharing a link visibly share it.
    """

    def _field(obj: object, name: str):
        """Read a field whether it arrived as a dict or an atproto model."""
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    found: list[str] = []
    for facet in _field(value, "facets") or []:
        for feature in _field(facet, "features") or []:
            if uri := _field(feature, "uri"):
                found.append(str(uri))
    found.extend(_URL_RE.findall(value.get("text", "") or ""))

    seen: list[str] = []
    for url in found:
        short = url.split("://", 1)[-1].rstrip("/")
        if len(short) > 60:
            short = short[:59] + "…"
        if short not in seen:
            seen.append(short)
    return seen[:3]


def _summarize(nsid: str, value: dict) -> str:
    """One-line salient summary of a record value, by NSID.

    Top-level posts show what they said. 41623ce stripped every post body to
    an action and a char count, to stop this block reflecting phi's register
    back at her as identity — the mirror it was taking down was
    [SELF-AWARENESS], a *characterization* written in her voice, and that one
    stays flat. A chronological log of what she published is a different
    thing, and without it she cannot tell that she has already said
    something: on 2026-07-25 she posted the same essay, with the same link,
    at 14:04 and again at 19:01.

    Replies stay summarised. They are high-volume, they are half of someone
    else's conversation, and they are not what she repeats.
    """
    if nsid == "app.bsky.feed.post":
        text = " ".join((value.get("text", "") or "").split())
        if value.get("reply"):
            return f"reply ({len(text)} chars)"
        said = text if len(text) <= POST_PREVIEW else text[: POST_PREVIEW - 1] + "…"
        line = f'top-level post: "{said}"'
        if links := _links_in(value):
            line += f" [linked: {', '.join(links)}]"
        return line
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
        # get_model_as_dict, not dict(): dict() is shallow, so nested
        # fields (facets, embeds, reply refs) stay as atproto model objects
        # and any .get() on them raises. docs/patterns.md, third occurrence.
        value = get_model_as_dict(rec.value) if rec.value else {}
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
        "[RECENT OPERATIONS — your last writes on PDS, chronological, so you "
        "always know what you have already said. this is a record of what "
        "went out, not a model for how to write: if something here is "
        "already covered, the reason to look is to avoid saying it twice, "
        "not to match its phrasing. replies are summarised; they are half of "
        "someone else's conversation.]"
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
