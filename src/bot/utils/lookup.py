"""Cold-contact lookup — fetch a stranger's profile + recent posts before replying.

This is the synchronous "let me see who you are" pre-reply behavior — it enriches
the current reply context with what's publicly visible right now, writing nothing.
"""

import logging

from bot.core.atproto_client import BotClient

logger = logging.getLogger("bot.lookup")


async def fetch_author_lookup(
    client: BotClient, handle: str, post_limit: int = 10
) -> str | None:
    """Fetch a handle's profile description + recent posts as formatted context.

    Returns formatted text suitable for injection into the agent's system prompt,
    or None on failure or if no useful data was retrieved. Best-effort: any error
    returns None rather than propagating, since this is enrichment, not gating.
    """
    await client.authenticate()

    profile_desc = ""
    follower_count: int | None = None
    follow_count: int | None = None
    post_count: int | None = None
    created_at = ""
    try:
        profile = client.client.app.bsky.actor.get_profile({"actor": handle})
        profile_desc = (getattr(profile, "description", "") or "").strip()
        follower_count = getattr(profile, "followers_count", None)
        follow_count = getattr(profile, "follows_count", None)
        post_count = getattr(profile, "posts_count", None)
        created_at = getattr(profile, "created_at", "") or ""
    except Exception as e:
        logger.debug(f"profile fetch failed for @{handle}: {e}")

    posts: list[str] = []
    try:
        feed = client.client.app.bsky.feed.get_author_feed(
            params={"actor": handle, "limit": post_limit, "filter": "posts_no_replies"}
        )
        for item in feed.feed:
            record = getattr(item.post, "record", None)
            text = getattr(record, "text", "") if record else ""
            indexed = getattr(item.post, "indexed_at", "") or ""
            ts = indexed[:16].replace("T", " ") if indexed else ""
            if text:
                posts.append(f"  [{ts}] {text}")
    except Exception as e:
        logger.debug(f"author feed fetch failed for @{handle}: {e}")

    if not profile_desc and not posts:
        return None

    parts = [
        f"[FIRST INTERACTION WITH @{handle} — phi looked at their public profile before responding]"
    ]

    profile_lines = []
    if profile_desc:
        profile_lines.append(f"bio: {profile_desc}")
    counts: list[str] = []
    if post_count is not None:
        counts.append(f"{post_count} posts")
    if follower_count is not None:
        counts.append(f"{follower_count} followers")
    if follow_count is not None:
        counts.append(f"following {follow_count}")
    if created_at:
        counts.append(f"joined {created_at[:10]}")
    if counts:
        profile_lines.append(" · ".join(counts))
    if profile_lines:
        parts.append("profile:")
        parts.extend(f"  {line}" for line in profile_lines)

    if posts:
        parts.append(f"recent posts (last {len(posts)}):")
        parts.extend(posts)

    parts.append(
        "use this to gauge who you're talking to. apply the same skepticism you'd "
        "apply to any stranger making claims — templated repetition, suspiciously round "
        "numerals across posts, links to a product they're selling, and 'we built X' "
        "without artifacts are tells. being curious is fine; granting credibility is earned."
    )

    return "\n".join(parts)
