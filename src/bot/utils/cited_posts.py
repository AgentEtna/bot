"""Cited-post extraction and resolution.

When a notification references another bluesky post — via a link facet or a
record-embed (quote / record_with_media) — surface the referenced post as a
structured citation alongside the notification. That lets ``post(in_reply_to=...)``
target it through the safe tool path instead of forcing phi to construct URLs from
prose text.

Two pieces:
  - :func:`extract_cited_references` walks a record and returns refs.
  - :func:`resolve_cited_entry` resolves a ref to a full context entry.

Handle→DID resolution uses :class:`atproto.AsyncIdResolver` (DNS + HTTP fallback,
which is the right thing for migrated handles).
"""

import logging
import re

from atproto import AsyncIdResolver, AtUri

from bot.utils.thread import resolve_facet_links

logger = logging.getLogger("bot.utils.cited_posts")

# bsky.app/profile/<handle-or-did>/post/<rkey>
_BSKY_POST_URL_RE = re.compile(
    r"https?://bsky\.app/profile/([^/\s]+)/post/([a-z0-9]+)",
    re.IGNORECASE,
)

_id_resolver = AsyncIdResolver()


def _parse_bsky_post_url(url: str) -> tuple[str, str] | None:
    """Return (handle_or_did, rkey) if url is a bsky.app post URL, else None."""
    m = _BSKY_POST_URL_RE.search(url.strip())
    return (m.group(1), m.group(2)) if m else None


def _ref_from_at_uri(uri: str) -> tuple[str, str] | None:
    """Return (did, rkey) for an app.bsky.feed.post at-uri, else None."""
    try:
        parsed = AtUri.from_str(uri)
    except Exception:
        return None
    if parsed.collection != "app.bsky.feed.post" or not parsed.rkey:
        return None
    return parsed.host, parsed.rkey


def extract_cited_references(record) -> list[dict]:
    """Walk a post record and return cited bsky-post references.

    Each ref is ``{"handle_or_did": str, "rkey": str, "source": str}``.
    Refs to the same (handle_or_did, rkey) are deduped. The caller resolves
    handles to DIDs via :func:`resolve_cited_entry`.
    """
    refs: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(handle_or_did: str, rkey: str, source: str) -> None:
        key = (handle_or_did, rkey)
        if key in seen:
            return
        seen.add(key)
        refs.append({"handle_or_did": handle_or_did, "rkey": rkey, "source": source})

    # link facets pointing at bsky.app posts
    for f in getattr(record, "facets", None) or []:
        for feat in getattr(f, "features", None) or []:
            if "link" not in (getattr(feat, "py_type", "") or ""):
                continue
            parsed = _parse_bsky_post_url(getattr(feat, "uri", "") or "")
            if parsed:
                _add(parsed[0], parsed[1], "facet")

    # embed: quote post or record_with_media — pull the cited at-uri out
    embed = getattr(record, "embed", None)
    if embed is not None:
        py_type = getattr(embed, "py_type", "") or ""
        rec = getattr(embed, "record", None)
        # record_with_media wraps an embed.record under .record
        if "record_with_media" in py_type and rec is not None:
            rec = getattr(rec, "record", None)
        elif "embed.record" not in py_type:
            rec = None
        if rec is not None:
            parsed = _ref_from_at_uri(getattr(rec, "uri", "") or "")
            if parsed:
                _add(parsed[0], parsed[1], "embed")

    return refs


async def resolve_cited_entry(client, ref: dict, cited_by_uri: str) -> dict | None:
    """Resolve a cited-ref into a notifications_context entry.

    Resolves handle→DID if needed (via ``AsyncIdResolver``), fetches the post
    via ``client.get_posts`` to get cid + reply refs, and returns an entry
    in the same shape used by ``post(in_reply_to=...)``.

    Returns ``None`` if the handle doesn't resolve or the post can't be fetched.
    """
    handle_or_did = ref.get("handle_or_did", "")
    rkey = ref.get("rkey", "")
    if not handle_or_did or not rkey:
        return None

    if handle_or_did.startswith("did:"):
        did = handle_or_did
    else:
        try:
            did = await _id_resolver.handle.resolve(handle_or_did)
        except Exception as e:
            logger.warning(f"handle resolve failed for {handle_or_did!r}: {e}")
            return None
        if not did:
            logger.warning(f"handle {handle_or_did!r} did not resolve to a DID")
            return None

    cited_uri = f"at://{did}/app.bsky.feed.post/{rkey}"

    try:
        posts_resp = await client.get_posts([cited_uri])
    except Exception as e:
        logger.warning(f"failed to fetch cited post {cited_uri}: {e}")
        return None
    if not posts_resp.posts:
        return None
    post = posts_resp.posts[0]

    if hasattr(post.record, "reply") and post.record.reply:
        root_uri = post.record.reply.root.uri
        root_cid = post.record.reply.root.cid
    else:
        root_uri = cited_uri
        root_cid = post.cid

    return {
        "uri": cited_uri,
        "cid": post.cid,
        "reason": "cited",
        "cited_by": cited_by_uri,
        "author_handle": post.author.handle,
        "author_did": getattr(post.author, "did", ""),
        "post_text": resolve_facet_links(post.record),
        "embed_desc": "",
        "image_urls": [],
        "root_uri": root_uri,
        "root_cid": root_cid,
        "thread_uri": root_uri,
        "thread_context": "",
        "indexed_at": getattr(post, "indexed_at", "") or "",
        "cited_refs": [],
    }
