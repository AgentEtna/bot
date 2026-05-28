"""Trusted posting tools — the only sanctioned path for phi to act on bluesky.

These tools are the side-effect layer of the agentic loop. They wrap
``bot_client`` operations with everything that needs to happen around a write:
mention-consent allowlists, reply-ref construction, memory writes, status
metrics, and grapheme-aware splitting (which lives in ``BotClient.create_post``).

The agent is told (in operational instructions) to use these tools instead of
raw atproto record tools via pdsx — the latter would bypass gating and could
accidentally tag arbitrary users via uncontrolled mention facets.

Target URIs (for replies, likes, reposts) are verified by fetching the record;
hallucinated URIs refuse cleanly. Posts already in the current notifications
batch short-circuit the fetch since their cid + author + thread root are
already loaded.
"""

import logging
from typing import Annotated

from atproto_client import models
from pydantic import Field
from pydantic_ai import RunContext

from bot.config import settings
from bot.core.atproto_client import bot_client
from bot.core.mentionable import get_mentionable_handles
from bot.status import bot_status
from bot.tools._helpers import PhiDeps

logger = logging.getLogger("bot.tools.posting")


async def _build_allowed_handles(*extra: str) -> set[str]:
    """Compute the mention-facet allowlist for a post.

    Always includes the bot owner, the bot itself, and anyone who has opted in
    via the mentionConsent record on phi's PDS. Extra handles (e.g. conversation
    participants) are added on top.
    """
    base = {settings.owner_handle, settings.bluesky_handle}
    try:
        base.update(await get_mentionable_handles())
    except Exception as e:
        logger.warning(f"failed to load mentionable handles: {e}")
    return base | {h for h in extra if h}


def _parse_at_uri(uri: str) -> tuple[str, str, str] | None:
    """Parse ``at://did/collection/rkey`` into ``(did, collection, rkey)``. None if malformed."""
    if not uri.startswith("at://"):
        return None
    parts = uri[5:].split("/", 2)
    if len(parts) != 3 or not all(parts):
        return None
    return parts[0], parts[1], parts[2]


async def _resolve_post_ref(
    uri: str, ctx_notifs: dict
) -> tuple[str, str, str, str, str] | None:
    """Return ``(parent_cid, root_uri, root_cid, author_handle, post_text)`` or None.

    Fast path: the URI is in the current notifications batch — cid, author,
    text, and thread root are already loaded.

    Fallback: fetch the record via ``get_record``. Used for replies to phi's
    own posts (threading) and for any other post URI phi got legitimately
    (e.g. from ``get_own_posts`` or ``search_posts``). If the record can't
    be fetched, the URI was probably hallucinated; return None.

    Author handle isn't resolved from a fresh fetch (would need an extra
    round trip); empty string is fine — the consent allowlist still gates
    mentions, and the memory write is skipped for out-of-batch replies.
    """
    entry = ctx_notifs.get(uri)
    if entry is not None:
        parent_cid = entry.get("cid", "") or ""
        return (
            parent_cid,
            entry.get("root_uri") or uri,
            entry.get("root_cid") or parent_cid,
            entry.get("author_handle", "") or "",
            entry.get("post_text", "") or "",
        )

    parsed = _parse_at_uri(uri)
    if not parsed:
        return None
    did, collection, rkey = parsed
    if collection != "app.bsky.feed.post":
        return None
    try:
        result = bot_client.client.com.atproto.repo.get_record(
            {"repo": did, "collection": collection, "rkey": rkey}
        )
    except Exception as e:
        logger.info(f"verify failed for {uri}: {e}")
        return None
    parent_cid = str(result.cid or "")
    if not parent_cid:
        return None
    value = dict(result.value) if result.value else {}
    reply = value.get("reply") if isinstance(value.get("reply"), dict) else None
    if reply and isinstance(reply.get("root"), dict):
        root_uri = str(reply["root"].get("uri") or uri)
        root_cid = str(reply["root"].get("cid") or parent_cid)
    else:
        root_uri = uri
        root_cid = parent_cid
    return parent_cid, root_uri, root_cid, "", ""


def register(agent):
    @agent.tool
    async def post(
        ctx: RunContext[PhiDeps],
        text: Annotated[
            str,
            Field(
                description=(
                    "the post text. lowercase per phi.md aesthetic. bsky's "
                    "300-grapheme limit is handled — longer text auto-splits "
                    "into a self-reply thread."
                )
            ),
        ],
        in_reply_to: Annotated[
            str,
            Field(
                description=(
                    "optional AT-URI of a post to reply to. omit (default '') "
                    "for a top-level post. when set, the tool fetches that "
                    "record to verify it exists and to derive the thread "
                    "root. works for any real bsky post, including your own "
                    "(threading), and refuses cleanly if the URI doesn't "
                    "resolve."
                )
            ),
        ] = "",
    ) -> str:
        """Create a post on bluesky. Top-level or reply — one operation.

        For threading: pass the URI of the parent post as ``in_reply_to``.
        Thread off your own posts (find URIs via ``get_own_posts``) or off
        anyone else's verified post.

        Handles facet construction (your @mentions notify only allowlisted
        handles), reply-ref construction (parent + root) when ``in_reply_to``
        is set, grapheme-aware splitting for long text, memory writes when
        you're replying to another author in your current notifications
        batch, and status recording.
        """
        if not in_reply_to:
            try:
                allowed = await _build_allowed_handles(ctx.deps.author_handle or "")
                await bot_client.create_post(text, allowed_handles=allowed)
                bot_status.record_response()
                logger.info(f"posted: {text[:80]}")
                return f"posted: {text[:100]}"
            except Exception as e:
                logger.exception(f"post failed: {e}")
                return f"failed to post: {e}"

        ref = await _resolve_post_ref(in_reply_to, ctx.deps.notifications_context or {})
        if ref is None:
            return (
                f"refused: could not verify {in_reply_to}. either it's not a "
                "valid AT-URI for a post, or the record can't be fetched."
            )
        parent_cid, root_uri, root_cid, author_handle, post_text = ref
        if not parent_cid:
            return f"refused: could not determine cid for {in_reply_to}"

        parent_ref = models.ComAtprotoRepoStrongRef.Main(
            uri=in_reply_to, cid=parent_cid
        )
        root_ref = models.ComAtprotoRepoStrongRef.Main(uri=root_uri, cid=root_cid)
        reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)

        try:
            allowed = await _build_allowed_handles(author_handle)
            result = await bot_client.create_post(
                text, reply_to=reply_ref, allowed_handles=allowed
            )
        except Exception as e:
            logger.exception(f"post (reply) failed for {in_reply_to}: {e}")
            return f"failed to post reply: {e}"

        bot_status.record_response()
        target = f"@{author_handle}" if author_handle else in_reply_to
        logger.info(f"replied to {target}: {text[:80]}")

        # store the exchange when the parent is from another author in the
        # current notifications batch (cited posts are in the batch too).
        # skip when threading your own posts or replying to URIs found
        # outside the batch — those aren't "interactions with a user."
        notifs = ctx.deps.notifications_context or {}
        if (
            in_reply_to in notifs
            and ctx.deps.memory
            and author_handle
            and author_handle != settings.bluesky_handle
        ):
            bot_post_uri = getattr(result, "uri", "") if result else ""
            sources = [u for u in (in_reply_to, bot_post_uri) if u]
            try:
                await ctx.deps.memory.after_interaction(
                    author_handle, post_text, text, source_uris=sources
                )
            except Exception as e:
                logger.warning(f"failed to store interaction for @{author_handle}: {e}")

        return f"replied to {target} at {in_reply_to}"

    @agent.tool
    async def like_post(
        ctx: RunContext[PhiDeps],
        uri: Annotated[
            str,
            Field(
                description=(
                    "AT-URI of the post to like. verified by fetch; refuses "
                    "cleanly if it doesn't resolve."
                )
            ),
        ],
    ) -> str:
        """Like a post. Use this to acknowledge something without saying anything."""
        ref = await _resolve_post_ref(uri, ctx.deps.notifications_context or {})
        if ref is None:
            return f"refused: could not verify {uri}"
        parent_cid, _, _, author_handle, _ = ref
        if not parent_cid:
            return f"refused: could not determine cid for {uri}"

        try:
            await bot_client.like_post(uri=uri, cid=parent_cid)
        except Exception as e:
            logger.exception(f"like_post failed for {uri}: {e}")
            return f"failed to like: {e}"

        bot_status.record_response()
        target = f"@{author_handle}" if author_handle else uri
        logger.info(f"liked {target}")
        return f"liked {uri}"

    @agent.tool
    async def repost_post(
        ctx: RunContext[PhiDeps],
        uri: Annotated[
            str,
            Field(
                description=(
                    "AT-URI of the post to repost. verified by fetch; refuses "
                    "cleanly if it doesn't resolve."
                )
            ),
        ],
    ) -> str:
        """Repost a post. Use rarely — only when something genuinely deserves amplification."""
        ref = await _resolve_post_ref(uri, ctx.deps.notifications_context or {})
        if ref is None:
            return f"refused: could not verify {uri}"
        parent_cid, _, _, author_handle, _ = ref
        if not parent_cid:
            return f"refused: could not determine cid for {uri}"

        try:
            await bot_client.repost(uri=uri, cid=parent_cid)
        except Exception as e:
            logger.exception(f"repost_post failed for {uri}: {e}")
            return f"failed to repost: {e}"

        bot_status.record_response()
        target = f"@{author_handle}" if author_handle else uri
        logger.info(f"reposted {target}")
        return f"reposted {uri}"
