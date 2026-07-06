"""[SEMBLE] — what phi's public library actually holds.

The old block was bare counts, which left live phi blind to its own
library: it couldn't build on, cite, or route around what exists without
spelunking through the semble tools. This block shows the shelf labels
(collection names + sizes) and the most recent cards, so saving and
filing decisions happen against real state.

Reads phi's own PDS directly (network-first, no appview dependency),
mirroring recent_operations.py. Cached 5min.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from bot.core.atproto_client import BotClient
from bot.utils.time import relative_when

logger = logging.getLogger("bot.public_memory")

RECENT_CARDS = 5

_BLOCK_TTL_SECONDS = 300  # 5min, mirrors core/self_state.py
_block_cache: dict = {"text": "", "fetched_at": 0.0}


def _list_records(client: BotClient, did: str, nsid: str) -> list[Any]:
    records: list[Any] = []
    cursor = None
    while True:
        try:
            response = client.client.com.atproto.repo.list_records(
                {"repo": did, "collection": nsid, "limit": 100, "cursor": cursor}
            )
        except Exception as e:
            logger.debug(f"list {nsid} failed: {e}")
            return records
        records.extend(response.records or [])
        cursor = response.cursor
        if not cursor:
            return records


def _card_line(record: Any) -> str:
    value = dict(record.value) if record.value else {}
    kind = (value.get("type") or value.get("kind") or "?").upper()
    content = value.get("content") or {}
    created = str(value.get("createdAt", ""))[:10]
    when = relative_when(value.get("createdAt", "")) or created
    if kind == "URL":
        url = content.get("url", "") if isinstance(content, dict) else ""
        title = ""
        if isinstance(content, dict):
            metadata = content.get("metadata") or {}
            if isinstance(metadata, dict):
                title = metadata.get("title", "")
        label = title or url
        return f"- [URL] {label[:100]} ({when})"
    text = content.get("text", "") if isinstance(content, dict) else ""
    snippet = " ".join(text.split())[:100]
    return f"- [{kind}] {snippet} ({when})"


def _render(
    collections: list[Any], links: list[Any], cards: list[Any], n_connections: int
) -> str:
    if not (collections or cards):
        return ""

    counts: dict[str, int] = {}
    for link in links:
        value = dict(link.value) if link.value else {}
        coll = value.get("collection") or {}
        uri = coll.get("uri", "") if isinstance(coll, dict) else ""
        if uri:
            counts[uri] = counts.get(uri, 0) + 1

    lines = [
        "[SEMBLE — your public library (cosmik). reference for saving and "
        "filing, not posting register.]"
    ]
    if collections:
        lines.append("collections:")
        for coll in collections:
            value = dict(coll.value) if coll.value else {}
            name = value.get("name", "untitled")
            n = counts.get(coll.uri, 0)
            lines.append(f"- {name} ({n} cards) [{coll.uri}]")
    if cards:
        # rkeys are TIDs — descending rkey = newest first.
        recent = sorted(cards, key=lambda r: r.uri.split("/")[-1], reverse=True)
        lines.append(f"most recent of {len(cards)} cards:")
        lines.extend(_card_line(r) for r in recent[:RECENT_CARDS])
    lines.append(f"({n_connections} connections)")
    return "\n".join(lines)


async def get_public_memory_block(client: BotClient) -> str:
    """Fetch + render the [SEMBLE] block. Cached 5min."""
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

    collections = _list_records(client, did, "network.cosmik.collection")
    links = _list_records(client, did, "network.cosmik.collectionLink")
    cards = _list_records(client, did, "network.cosmik.card")
    connections = _list_records(client, did, "network.cosmik.connection")

    block = _render(collections, links, cards, len(connections))
    _block_cache["text"] = block
    _block_cache["fetched_at"] = now
    return block
