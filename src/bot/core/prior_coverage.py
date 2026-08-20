"""Prior-coverage recall — phi's own posts, keyed by what she's perceiving.

On 2026-08-05 22:02 and 2026-08-06 22:02 phi posted the same summary of the
same gracekind post, same link, because nothing in the system could answer
"have I ever said anything about this?" — her only self-record was a
10-row recency window, and her memory namespaces store observations about
*other people*. This module is the missing organ: a semantic index of her
own published posts, queried by content the moment it enters her context
(feed reads, searches, notification batches), so recall fires the way a
person's does — the sight of the material reminds her she already covered
it. The same lookup runs once more at posting time with the draft itself
as the query (bot/tools/posting.py → policy judge, `self-repeat`): on
2026-08-18 the perception-keyed pass fired on a whole feed blob and
surfaced five chicken-market posts while missing the two-day-old gerakines
post phi was about to restate. The draft is the one query that is exactly
the thing being checked.

Index lives in turbopuffer (phi-own-posts). Freshness has two halves:
- backfill (startup task): pages her PDS post collection, indexes anything
  newer than the /data watermark — covers history and downtime gaps.
- live: the jetstream ops-log consumer indexes each post create/update as
  it commits.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atproto_client.models.utils import get_model_as_dict

from bot.utils.time import relative_when

if TYPE_CHECKING:
    from bot.core.atproto_client import BotClient
    from bot.memory.namespace_memory import NamespaceMemory

logger = logging.getLogger("bot.prior_coverage")

NAMESPACE = "phi-own-posts"
SCHEMA = {
    "text": {"type": "string", "full_text_search": True},
    "links": {"type": "[]string", "filterable": True},
    "is_reply": {"type": "bool", "filterable": True},
    "created_at": {"type": "string", "filterable": True},
}

WATERMARK_FILE = Path("/data/own_posts_watermark.json")

DISTANCE_THRESHOLD = 0.45
"""Cosine distance below which a prior post is surfaced. An exact link
match is surfaced regardless of distance — a shared URL is the single
strongest signal a subject is already covered."""

TOP_K = 5
PREVIEW = 200

_URL_RE = re.compile(r"https?://[^\s<>\")\]]+")


def extract_links(value: dict[str, Any]) -> list[str]:
    """Full URLs from a post record's facets + text, normalized (no scheme,
    no trailing slash) so facet URIs and display strings compare equal."""
    found: list[str] = []
    for facet in value.get("facets") or []:
        for feature in (
            facet.get("features") if isinstance(facet, dict) else None
        ) or []:
            uri = feature.get("uri") if isinstance(feature, dict) else None
            if uri:
                found.append(str(uri))
    found.extend(_URL_RE.findall(value.get("text", "") or ""))
    normalized = []
    for url in found:
        short = url.split("://", 1)[-1].rstrip("/")
        if short and short not in normalized:
            normalized.append(short)
    return normalized


def links_in_text(text: str) -> list[str]:
    return extract_links({"text": text})


def _namespace(memory: NamespaceMemory):
    return memory.client.namespace(NAMESPACE)


async def index_post_value(
    memory: NamespaceMemory, rkey: str, value: dict[str, Any]
) -> None:
    """Index one app.bsky.feed.post record value. id = rkey, so re-indexing
    (backfill overlap, jetstream replay, edits) is idempotent."""
    text = (value.get("text") or "").strip()
    if not text:
        return
    _namespace(memory).write(
        upsert_rows=[
            {
                "id": rkey,
                "vector": await memory.embed(text),
                "text": text,
                "links": extract_links(value),
                "is_reply": bool(value.get("reply")),
                "created_at": str(
                    value.get("createdAt") or value.get("created_at") or ""
                ),
            }
        ],
        distance_metric="cosine_distance",
        schema=SCHEMA,
    )


async def search_own_posts(
    memory: NamespaceMemory, query_text: str, top_k: int = 5
) -> list[dict]:
    """Nearest of phi's own top-level posts to *query_text*, with distances."""
    try:
        # replies included: "have I said this" must cover everything phi
        # has said, not just top-level posts — a point made five times in
        # threads used to look never-made (operator feedback, 2026-08-12)
        response = _namespace(memory).query(
            rank_by=("vector", "ANN", await memory.embed(query_text)),
            top_k=top_k,
            include_attributes=["text", "links", "created_at", "is_reply"],
        )
        return [
            {
                "rkey": row.id,
                "text": row.text,
                "links": getattr(row, "links", []) or [],
                "created_at": getattr(row, "created_at", ""),
                "is_reply": bool(getattr(row, "is_reply", False)),
                "distance": row["$dist"],
            }
            for row in response.rows or []
        ]
    except Exception as e:
        if "was not found" in str(e):
            return []
        raise


def _read_watermark() -> str:
    try:
        return json.loads(WATERMARK_FILE.read_text())["rkey"]
    except Exception:
        return ""


def _write_watermark(rkey: str) -> None:
    try:
        WATERMARK_FILE.parent.mkdir(parents=True, exist_ok=True)
        WATERMARK_FILE.write_text(json.dumps({"rkey": rkey}))
    except Exception as e:
        logger.warning(f"failed to persist own-posts watermark: {e}")


async def backfill_own_posts(client: BotClient, memory: NamespaceMemory) -> int:
    """Index every post newer than the watermark; returns count indexed.

    Pages listRecords newest-first (rkeys are TIDs, so lexicographic order
    is chronological) and stops at the watermark. First run walks the whole
    collection — a few thousand embeddings, done once.
    """
    await client.authenticate()
    if not client.client.me:
        return 0
    did = client.client.me.did
    watermark = _read_watermark()

    indexed = 0
    newest_seen = ""
    cursor: str | None = None
    done = False
    while not done:
        params: dict[str, Any] = {
            "repo": did,
            "collection": "app.bsky.feed.post",
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor
        response = client.client.com.atproto.repo.list_records(params)
        records = response.records or []
        if not records:
            break
        for rec in records:
            rkey = rec.uri.split("/")[-1]
            if watermark and rkey <= watermark:
                done = True
                break
            newest_seen = newest_seen or rkey
            value = get_model_as_dict(rec.value) if rec.value else {}
            try:
                await index_post_value(memory, rkey, value)
                indexed += 1
            except Exception as e:
                logger.warning(f"backfill index failed for {rkey}: {e}")
        cursor = response.cursor
        if not cursor:
            break

    if newest_seen:
        _write_watermark(newest_seen)
    if indexed:
        logger.info(f"own-posts backfill indexed {indexed} posts")
    return indexed


def render_coverage(hits: list[dict], candidate_links: list[str]) -> str:
    """Pure renderer: the [PRIOR COVERAGE] note for a set of search hits.

    A hit renders when it's semantically close (distance under threshold)
    or shares a link with the candidate material. Empty string when
    nothing qualifies — the common case, and the block should be absent
    rather than noisy.
    """
    lines: list[str] = []
    for hit in hits:
        shared = sorted(set(hit.get("links") or []) & set(candidate_links))
        distance = hit.get("distance")
        close = distance is not None and distance <= DISTANCE_THRESHOLD
        if not (close or shared):
            continue
        ts = hit.get("created_at", "")
        when = relative_when(ts) if ts else ""
        kind = "reply" if hit.get("is_reply") else "top-level post"
        stamp = (
            f"{ts[:16]} ({when}, {kind})"
            if ts and when
            else f"{ts or 'undated'} ({kind})"
        )
        text = " ".join((hit.get("text") or "").split())
        if len(text) > PREVIEW:
            text = text[: PREVIEW - 1] + "…"
        line = f'- {stamp}: "{text}"'
        if shared:
            line += f" [SAME LINK: {', '.join(shared)}]"
        lines.append(line)
    if not lines:
        return ""
    return (
        "[PRIOR COVERAGE — your own posts nearest this material. if what "
        "you were about to say is already here, it has been said; repeat "
        "it only on purpose, and by referencing the earlier post.]\n" + "\n".join(lines)
    )


async def coverage_note(memory: NamespaceMemory | None, material: str) -> str:
    """The [PRIOR COVERAGE] note for a blob of perceived material.

    One embedding call per invocation. Failures degrade to absence —
    recall going quiet must never break perception itself.
    """
    material = (material or "").strip()
    if not memory or not material:
        return ""
    try:
        hits = await search_own_posts(memory, material[:6000], top_k=TOP_K)
        return render_coverage(hits, links_in_text(material))
    except Exception as e:
        logger.warning(f"prior-coverage recall failed: {e}")
        return ""
