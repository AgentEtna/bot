"""[DISCOVERY POOL] — authors the operator has been liking lately.

A generic, service-owned signal: the operator's likes are high-trust
attention. The endpoint (hub) exposes recently-liked authors with sample
posts; phi filters out anyone she's already exchanged with and surfaces
the rest as warm leads — strangers worth considering, not cold outreach.

Coupling stays at the JSON contract: the source service owns the data
model and refresh, phi owns the per-consumer filter. Renderer is split
from fetch+filter so a future templating swap only touches `_render`.
"""

import logging
import time
from typing import TypedDict

import httpx

from bot.config import settings
from bot.memory import NamespaceMemory

logger = logging.getLogger("bot.discovery_pool")

TOP_N = 8
TEXT_TRUNCATE = 140
SAMPLE_LIMIT = 3
HTTP_TIMEOUT = 10
_BLOCK_TTL_SECONDS = 300  # 5min, mirrors other PDS state blocks
_block_cache: dict = {"text": "", "fetched_at": 0.0}


class _SamplePost(TypedDict):
    uri: str
    text: str
    liked_at: str


class _Entry(TypedDict):
    handle: str
    did: str
    likes_in_window: int
    last_liked_at: str
    sample_posts: list[_SamplePost]


def _short(text: str, n: int = TEXT_TRUNCATE) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


async def _fetch_pool() -> list[_Entry]:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(settings.discovery_pool_url)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.debug(f"discovery pool fetch failed: {e}")
        return []
    if not isinstance(data, list):
        logger.warning(f"discovery pool returned non-list: {type(data).__name__}")
        return []
    return data  # type: ignore[return-value]


async def _has_interaction(memory: NamespaceMemory, handle: str) -> bool:
    """True if phi has any stored interaction record with this handle."""
    try:
        ns = memory.get_user_namespace(handle)
        response = ns.query(
            rank_by=("created_at", "desc"),
            top_k=1,
            filters=[["kind", "Eq", "interaction"]],
            include_attributes=["kind"],
        )
        return bool(response.rows)
    except Exception:
        return False  # namespace doesn't exist yet → no interactions


def _render(entries: list[_Entry]) -> str:
    if not entries:
        return ""
    lines = [
        "[DISCOVERY POOL — strangers (to you) whose posts the operator has "
        "been liking lately. the prose previews below are operator-attention "
        "shadow, not your own reads — useful for identifying who to engage "
        "with, NOT raw material to riff on. if a take here ends up in one "
        "of your posts, cite the author. if you find yourself elaborating "
        "an idea you only met because the operator liked it, that's an echo "
        "— push past it, or attribute it. warm leads, not cold.]"
    ]
    for e in entries:
        likes = e.get("likes_in_window", 0)
        last = e.get("last_liked_at", "")
        lines.append("")
        lines.append(
            f"@{e['handle']} — {likes} like{'s' if likes != 1 else ''} from operator"
            f"{f' (last: {last[:10]})' if last else ''}"
        )
        for post in (e.get("sample_posts") or [])[:SAMPLE_LIMIT]:
            text = _short(post.get("text") or "")
            if text:
                lines.append(f"  · {text!r}")
    return "\n".join(lines)


async def get_filtered_pool(
    memory: NamespaceMemory | None, top_n: int = TOP_N
) -> list[_Entry]:
    """Fetch the operator-likes pool, drop self + handles phi has already
    interacted with, return the top-N. This is the canonical "what phi
    actually sees in her prompt" view; the JSON API endpoint and the
    rendered prompt block both compose from this single source of truth.
    """
    raw = await _fetch_pool()
    if not raw:
        return []

    if memory is not None:
        kept: list[_Entry] = []
        for entry in raw:
            handle = entry.get("handle", "")
            if not handle or handle == settings.bluesky_handle:
                continue
            if await _has_interaction(memory, handle):
                continue
            kept.append(entry)
        raw = kept

    return raw[:top_n]


async def get_discovery_pool_block(memory: NamespaceMemory | None) -> str:
    """Fetch + filter + render the [DISCOVERY POOL] block. Cached 5min."""
    now = time.time()
    if _block_cache["text"] and now - _block_cache["fetched_at"] < _BLOCK_TTL_SECONDS:
        return _block_cache["text"]

    entries = await get_filtered_pool(memory)
    block = _render(entries)
    _block_cache["text"] = block
    _block_cache["fetched_at"] = now
    return block
