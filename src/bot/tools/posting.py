"""Trusted posting tools — the only sanctioned path for phi to act on bluesky.

These tools are the side-effect layer of the agentic loop. They wrap
``bot_client`` operations with everything that needs to happen around a write:
mention-consent allowlists, reply-ref construction, memory writes, status
metrics, and grapheme-aware splitting (which lives in ``BotClient.create_post``).

The agent is told (in operational instructions) to use these tools instead of
the raw atproto record tools available via the pdsx MCP. The pdsx tools would
bypass the gating and could accidentally tag arbitrary users via uncontrolled
mention facets.

Each ``reply_to`` / ``like_post`` / ``repost_post`` call is scoped to URIs the
agent saw in its current notifications batch — the tool refuses to act on a URI
that isn't in ``ctx.deps.notifications_context``. This prevents the model from
hallucinating a target URI and posting somewhere unrelated. The notifications
context also includes posts *cited by* the current batch (link facets and
quote-embeds), so the operator-directed "reply to that post" pattern works
through the safe tool path without requiring the agent to construct URIs from
prose.
"""

import logging

from atproto_client import models
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


def register(agent):
    @agent.tool
    async def reply_to(ctx: RunContext[PhiDeps], uri: str, text: str) -> str:
        """Reply to a specific post in your current notifications batch.

        Use this for mentions, replies, and quotes — anything where someone is
        talking to you or about you. The URI must be in your current
        [NEW NOTIFICATIONS] block. Cited posts (rendered as ``cited: @author
        [uri]: ...`` lines under a notification) are also valid targets, so
        the "operator told me to reply to that post" pattern works here too —
        pass the cited URI verbatim, don't construct one from prose.

        This tool handles facet construction (your mentions only become real
        notifying tags for handles in the consent allowlist), reply-ref
        construction (parent + root), grapheme-aware splitting for long text,
        memory writes (the exchange is stored), and status recording.

        uri: the URI of the post you're replying to — must appear in the current
            [NEW NOTIFICATIONS] block, either as a notification or a cited line
        text: your reply text — written naturally, no need to construct facets manually
        """
        notifs = ctx.deps.notifications_context or {}
        entry = notifs.get(uri)
        if entry is None:
            return (
                f"refused: {uri} is not in your current notifications batch. "
                f"reply_to only works on URIs you saw in [NEW NOTIFICATIONS]."
            )

        cid = entry.get("cid")
        author_handle = entry.get("author_handle")
        post_text = entry.get("post_text", "")
        if not cid or not author_handle:
            return f"refused: notifications context entry for {uri} is missing cid or author"

        parent_ref = models.ComAtprotoRepoStrongRef.Main(uri=uri, cid=cid)
        root_uri = entry.get("root_uri") or uri
        root_cid = entry.get("root_cid") or cid
        root_ref = models.ComAtprotoRepoStrongRef.Main(uri=root_uri, cid=root_cid)
        reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)

        try:
            allowed = await _build_allowed_handles(author_handle)
            result = await bot_client.create_post(
                text, reply_to=reply_ref, allowed_handles=allowed
            )
        except Exception as e:
            logger.exception(f"reply_to failed for {uri}: {e}")
            return f"failed to post reply: {e}"

        bot_status.record_response()
        logger.info(f"replied to @{author_handle}: {text[:80]}")

        # store the exchange in memory so phi remembers it next time. cite
        # both ends of the exchange so future observations extracted from
        # this interaction inherit real provenance.
        if ctx.deps.memory:
            bot_post_uri = getattr(result, "uri", "") if result else ""
            sources = [u for u in (uri, bot_post_uri) if u]
            try:
                await ctx.deps.memory.after_interaction(
                    author_handle, post_text, text, source_uris=sources
                )
            except Exception as e:
                logger.warning(f"failed to store interaction for @{author_handle}: {e}")

        return f"replied to @{author_handle} at {uri}"

    @agent.tool
    async def like_post(ctx: RunContext[PhiDeps], uri: str) -> str:
        """Like a post from your current notifications batch.

        Use this when you want to acknowledge something without saying anything.
        The URI must be from your current [NEW NOTIFICATIONS] block.
        """
        notifs = ctx.deps.notifications_context or {}
        entry = notifs.get(uri)
        if entry is None:
            return (
                f"refused: {uri} is not in your current notifications batch. "
                f"like_post only works on URIs you saw in [NEW NOTIFICATIONS]."
            )

        cid = entry.get("cid")
        if not cid:
            return f"refused: notifications context entry for {uri} is missing cid"

        try:
            await bot_client.like_post(uri=uri, cid=cid)
        except Exception as e:
            logger.exception(f"like_post failed for {uri}: {e}")
            return f"failed to like: {e}"

        bot_status.record_response()
        author = entry.get("author_handle", "?")
        logger.info(f"liked @{author}'s post {uri}")
        return f"liked {uri}"

    @agent.tool
    async def repost_post(ctx: RunContext[PhiDeps], uri: str) -> str:
        """Repost a post from your current notifications batch.

        Use this rarely — only when something genuinely deserves amplification.
        The URI must be from your current [NEW NOTIFICATIONS] block.
        """
        notifs = ctx.deps.notifications_context or {}
        entry = notifs.get(uri)
        if entry is None:
            return (
                f"refused: {uri} is not in your current notifications batch. "
                f"repost_post only works on URIs you saw in [NEW NOTIFICATIONS]."
            )

        cid = entry.get("cid")
        if not cid:
            return f"refused: notifications context entry for {uri} is missing cid"

        try:
            await bot_client.repost(uri=uri, cid=cid)
        except Exception as e:
            logger.exception(f"repost_post failed for {uri}: {e}")
            return f"failed to repost: {e}"

        bot_status.record_response()
        author = entry.get("author_handle", "?")
        logger.info(f"reposted @{author}'s post {uri}")
        return f"reposted {uri}"
