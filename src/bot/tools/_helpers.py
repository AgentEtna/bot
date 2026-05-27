"""Shared types and utilities for phi's tools."""

import logging
from dataclasses import dataclass
from datetime import date

import httpx
from pydantic_ai import RunContext

from bot.config import settings
from bot.memory import NamespaceMemory

logger = logging.getLogger("bot.tools")


# --- deps ---


@dataclass
class PhiDeps:
    """Typed dependencies passed to every tool via RunContext."""

    author_handle: str
    memory: NamespaceMemory | None = None
    thread_uri: str | None = None
    thread_context: str | None = None
    # batch-of-notifications context: maps notification post URI -> per-notif data
    # populated by the message handler before calling agent.run; consumed by the
    # trusted posting tools (reply_to / like_post / repost_post) to look up cids,
    # parent/root refs, author handles, post text, etc, and by the dynamic system
    # prompts to format the notifications block + per-author memory blocks.
    notifications_context: dict | None = None


def _is_owner(ctx: RunContext[PhiDeps]) -> bool:
    """Check if the bot's owner is participating in this interaction.

    Single-message mode: direct author_handle check.

    Batch mode (author_handle is empty): unlock only when the owner liked
    or reposted one of phi's posts AND no other authors are present in
    the batch. The "no other authors" guard eliminates the cross-request
    exploit where a stranger's owner-gated request would inherit
    authorization from an unrelated owner like in the same poll window.
    If a stranger is in the batch, likes don't authorize — just re-like
    after the batch clears.
    """
    if ctx.deps.author_handle == settings.owner_handle:
        return True
    if not ctx.deps.notifications_context:
        return False

    authors = {e.get("author_handle") for e in ctx.deps.notifications_context.values()}
    # any author other than the owner or phi itself means batch is mixed
    if authors - {settings.owner_handle, settings.bluesky_handle}:
        return False

    return any(
        e.get("author_handle") == settings.owner_handle
        and e.get("reason") in ("like", "repost")
        for e in ctx.deps.notifications_context.values()
    )


# --- formatting ---


def _relative_age(timestamp: str, today: date) -> str:
    """Turn an ISO timestamp into a human-readable age like '2y ago' or '3d ago'."""
    try:
        post_date = date.fromisoformat(timestamp[:10])
    except (ValueError, TypeError):
        return ""
    delta = today - post_date
    days = delta.days
    if days < 0:
        return ""
    if days == 0:
        return "today"
    if days == 1:
        return "1d ago"
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    years = days // 365
    remaining_months = (days % 365) // 30
    if remaining_months:
        return f"{years}y {remaining_months}mo ago"
    return f"{years}y ago"


def _post_url(uri: str, handle: str) -> str:
    """Convert an AT-URI to a bsky.app URL."""
    # at://did:plc:.../app.bsky.feed.post/rkey -> https://bsky.app/profile/handle/post/rkey
    rkey = uri.split("/")[-1] if "/" in uri else ""
    return f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else ""


def _format_feed_posts(feed_posts, limit: int = 20) -> str:
    """Format feed posts into a readable summary."""
    today = date.today()
    lines = []
    for item in feed_posts[:limit]:
        post = item.post
        text = post.record.text if hasattr(post.record, "text") else ""
        handle = post.author.handle
        likes = post.like_count or 0
        url = _post_url(post.uri, handle)
        age = (
            _relative_age(post.indexed_at, today)
            if hasattr(post, "indexed_at") and post.indexed_at
            else ""
        )
        age_str = f", {age}" if age else ""
        lines.append(f"@{handle} ({likes} likes{age_str}): {text[:200]}\n  {url}")
    return "\n\n".join(lines)


def _short_date(iso: str) -> str:
    """Extract YYYY-MM-DD from an ISO timestamp, or return '' if missing."""
    return iso[:10] if iso and len(iso) >= 10 else ""


def _format_user_results(results: list[dict], handle: str) -> list[str]:
    parts = []
    for r in results:
        kind = r.get("kind", "unknown")
        content = r.get("content", "")
        tags = r.get("tags", [])
        tag_str = f"[{', '.join(tags)}]" if tags else ""
        date = _short_date(r.get("created_at", ""))
        date_str = f" ({date})" if date else ""
        parts.append(f"[{kind}]{tag_str}{date_str} {content}")
    return parts


def _format_episodic_results(results: list[dict]) -> list[str]:
    parts = []
    for r in results:
        tags = f" [{', '.join(r['tags'])}]" if r.get("tags") else ""
        date = _short_date(r.get("created_at", ""))
        date_str = f" ({date})" if date else ""
        parts.append(f"[note]{tags}{date_str} {r['content']}")
    return parts


def _format_unified_results(results: list[dict], handle: str) -> list[str]:
    parts = []
    for r in results:
        source = r.get("_source", "")
        content = r.get("content", "")
        tags = r.get("tags", [])
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        date = _short_date(r.get("created_at", ""))
        date_str = f" ({date})" if date else ""
        if source == "user":
            kind = r.get("kind", "unknown")
            parts.append(f"[@{handle} {kind}]{tag_str}{date_str} {content}")
        else:
            parts.append(f"[note]{tag_str}{date_str} {content}")
    return parts


# --- infrastructure ---

EVERGREEN_PROXY = "https://evergreen-proxy.nate-8fe.workers.dev"
SERVICE_CHECKS = [
    {"url": "https://api.plyr.fm/health", "name": "plyr api"},
    {"url": "https://plyr.fm", "name": "plyr frontend"},
    {"url": "https://pds.zzstoatzz.io/xrpc/_health", "name": "PDS"},
    {"url": "https://prefect-server.waow.tech/api/health", "name": "prefect"},
    {"url": "https://prefect-metrics.waow.tech/api/health", "name": "grafana"},
    {"url": "https://relay.waow.tech/xrpc/_health", "name": "indigo relay"},
    {"url": "https://zlay.waow.tech/_health", "name": "zlay"},
    {"url": "https://coral.fly.dev/health", "name": "trending"},
    {
        "url": "https://leaflet-search-backend.fly.dev/health",
        "name": "standard.site backend",
    },
    {"url": "https://pub-search.waow.tech", "name": "pub-search"},
    {"url": "https://typeahead.waow.tech/stats", "name": "typeahead"},
    {"url": "https://zig-bsky-feed.fly.dev/health", "name": "music-feed"},
    {"url": "https://pollz-backend.fly.dev/health", "name": "pollz"},
]


async def _check_services_impl() -> str:
    """Hit the evergreen proxy with all service checks. Returns formatted status."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.post(
                EVERGREEN_PROXY,
                json={"checks": SERVICE_CHECKS},
            )
            r.raise_for_status()
            results = r.json()
        except Exception as e:
            return f"evergreen proxy unreachable: {e}"

    failures: list[str] = []
    healthy: list[str] = []

    checks = results if isinstance(results, list) else results.get("results", [])
    # build name lookup from our request
    name_by_url = {c["url"]: c["name"] for c in SERVICE_CHECKS}

    for check in checks:
        url = check.get("url", "")
        name = name_by_url.get(url, url)
        status = check.get("status")
        ms = check.get("ms", "?")
        ok = check.get("ok", False)

        if ok:
            healthy.append(f"{name}: ok ({ms}ms)")
        else:
            error = check.get("error", f"status {status}")
            failures.append(f"{name}: DOWN ({error})")

    parts: list[str] = []
    if failures:
        parts.append("FAILURES:\n" + "\n".join(failures))
    parts.append(f"{len(healthy)}/{len(healthy) + len(failures)} services healthy")
    if not failures:
        parts.append("\n".join(healthy))

    return "\n".join(parts)
