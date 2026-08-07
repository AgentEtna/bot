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

from bot.core import ops_log
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

PER_COLLECTION_LIMIT = 25
WINDOW_HOURS = 48.0
"""Wall-clock bound, replacing the old TOP_N=10 count bound. A count bound
silently means "the last few hours" on a busy day — on 2026-08-06 phi
re-posted a 24h-old subject verbatim because eleven newer writes had
scrolled it out of a 10-row window. Cost now scales with her activity,
which is honest: a chattier phi has more to be aware of."""

MAX_ROWS = 80
"""Sanity cap within the window; truncation is announced in the header."""

_BLOCK_TTL_SECONDS = 300  # 5min, mirrors core/self_state.py
_block_cache: dict = {"text": "", "fetched_at": 0.0}


class _Row(TypedDict):
    rkey: str
    nsid: str
    created_at: str
    summary: str
    op: str  # create | update | delete
    local: bool  # written by this process (attribution is best-effort)


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
                op="create",
                local=False,
            )
        )
    return rows


def _rows_from_ops(ops: list[ops_log.OpRow]) -> list[_Row]:
    """Convert event-log ops to render rows.

    Deletes have no record body on the wire; if the create/update was
    logged inside the window, its summary is echoed so phi sees WHAT
    vanished, not just that something did.
    """
    last_summary: dict[tuple[str, str], str] = {}
    rows: list[_Row] = []
    for op in ops:
        key = (op["nsid"], op["rkey"])
        if op["op"] == "delete":
            prior = last_summary.get(key, "")
            summary = f"was: {prior}" if prior else "(record body not logged)"
        else:
            value = op["record"] or {}
            summary = _summarize(op["nsid"], value) if value else ""
            if summary:
                last_summary[key] = summary
        rows.append(
            _Row(
                rkey=op["rkey"],
                nsid=op["nsid"],
                created_at=op["at"],
                summary=summary,
                op=op["op"],
                local=op["local"],
            )
        )
    return rows


def _merge(event_rows: list[_Row], snapshot_rows: list[_Row]) -> list[_Row]:
    """Event-log rows win over snapshot rows for the same record; the
    snapshot only fills creates the log missed (downtime, pre-log history)."""
    seen = {(r["nsid"], r["rkey"]) for r in event_rows}
    merged = list(event_rows)
    merged.extend(
        r for r in snapshot_rows if (r["nsid"], r["rkey"]) not in seen
    )
    merged.sort(key=lambda r: r["created_at"])
    return merged


_OP_TAGS = {"create": "", "update": "EDITED", "delete": "DELETED"}

_REPLY_RE = re.compile(r"^reply \(\d+ chars\)$")


def _compact(rows: list[_Row]) -> list[_Row]:
    """Collapse rows that carry no distinct information.

    - consecutive reply rows → one `replies ×N` row (timestamp of the last)
    - a NOTE card created within a minute of a URL card → folded into the
      URL card's row (`… +note`); semble saves always write the pair, so
      two rows per save was pure double-billing
    """
    out: list[_Row] = []
    for r in rows:
        prev = out[-1] if out else None
        if (
            prev is not None
            and r["nsid"] == "app.bsky.feed.post"
            and prev["nsid"] == "app.bsky.feed.post"
            and r["op"] == prev["op"] == "create"
            and _REPLY_RE.match(r["summary"])
            and (_REPLY_RE.match(prev["summary"]) or prev["summary"].startswith("replies ×"))
        ):
            n = (
                int(prev["summary"].split("×")[1]) + 1
                if prev["summary"].startswith("replies ×")
                else 2
            )
            out[-1] = {**prev, "created_at": r["created_at"], "summary": f"replies ×{n}"}
            continue
        if (
            prev is not None
            and r["nsid"] == prev["nsid"] == "network.cosmik.card"
            and r["op"] == prev["op"] == "create"
            and r["summary"] == "NOTE card"
            and prev["summary"].startswith("URL card")
            and r["created_at"][:16] == prev["created_at"][:16]
        ):
            out[-1] = {**prev, "summary": prev["summary"] + " +note"}
            continue
        out.append(r)
    return out


def _render(rows: list[_Row], truncated: int = 0) -> str:
    """Render rows as the [RECENT OPERATIONS] block. Pure function — easy to template later."""
    if not rows:
        return ""
    rows = _compact(rows)
    nsid_width = max(len(r["nsid"]) for r in rows)
    header = (
        "[RECENT OPERATIONS — your repo's last "
        f"{WINDOW_HOURS:.0f}h, chronological: what you already said, so you "
        "don't say it twice (a record, not a style model). EDITED/DELETED "
        "are repo events; 'not via this process' means your hosted tools or "
        "an external service — flag any you don't recognise.]"
    )
    if truncated:
        header += f" (showing newest {len(rows)}; {truncated} older rows elided)"
    lines = [header]
    for r in rows:
        ts = r["created_at"]
        when = relative_when(ts) if ts else ""
        time_part = f"{ts[:19]}Z ({when})" if ts and when else (ts or "")
        nsid_part = r["nsid"].ljust(nsid_width)
        tag = _OP_TAGS.get(r["op"], "")
        if tag and not r["local"]:
            tag += " (not via this process)"
        tag_part = f"{tag}  " if tag else ""
        lines.append(f"{time_part}  {nsid_part}  {tag_part}{r['summary']}")
    return "\n".join(lines)


async def get_operations_block(client: BotClient) -> str:
    """Fetch + render the [RECENT OPERATIONS] block. Cached 5min.

    Primary source is the jetstream-backed ops log (real events: creates,
    edits, deletes). The listRecords snapshot backfills creates the log
    missed while the process was down.
    """
    now = time.time()
    if _block_cache["text"] and now - _block_cache["fetched_at"] < _BLOCK_TTL_SECONDS:
        return _block_cache["text"]

    try:
        event_rows = _rows_from_ops(ops_log.read_ops(WINDOW_HOURS))
    except Exception as e:
        logger.warning(f"ops log read failed: {e}")
        event_rows = []
    event_rows = [r for r in event_rows if r["nsid"] in MEANINGFUL_COLLECTIONS]

    snapshot_rows: list[_Row] = []
    try:
        await client.authenticate()
        if client.client.me:
            did = client.client.me.did
            cutoff = time.time() - WINDOW_HOURS * 3600
            for nsid in MEANINGFUL_COLLECTIONS:
                for row in _fetch_collection(client, did, nsid):
                    ts = row["created_at"]
                    try:
                        from datetime import datetime

                        in_window = (
                            datetime.fromisoformat(ts).timestamp() >= cutoff
                        )
                    except ValueError:
                        in_window = False
                    if in_window:
                        snapshot_rows.append(row)
    except Exception as e:
        logger.warning(f"snapshot backfill failed: {e}")

    merged = _merge(event_rows, snapshot_rows)
    truncated = max(0, len(merged) - MAX_ROWS)
    block = _render(merged[-MAX_ROWS:], truncated=truncated)
    _block_cache["text"] = block
    _block_cache["fetched_at"] = now
    return block
