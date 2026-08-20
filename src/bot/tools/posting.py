"""Trusted posting tools — the only sanctioned path for phi to act on bluesky.

These tools are the side-effect layer of the agentic loop. They wrap
``bot_client`` operations with everything that needs to happen around a write:
mention-consent allowlists, reply-ref construction, memory writes, status
metrics, and grapheme-aware splitting (which lives in ``BotClient.create_post``).

The agent is told (in operational instructions) to use these tools instead of
raw atproto record tools via pdsx — the latter would bypass gating and could
accidentally tag arbitrary users via uncontrolled mention facets.

Target URIs for replies are verified by fetching the record; reactions
(likes, reposts) live in bot/core/mcp_guard.py as governed pdsx writes.
hallucinated URIs refuse cleanly. Posts already in the current notifications
batch short-circuit the fetch since their cid + author + thread root are
already loaded.

``post`` additionally runs through the pre-action policy judge
(``bot.core.policy``): an independent model reviews the proposed post plus its
provenance (invited vs unprompted) against phi's written policies and can
block or warn. The verdict comes back to phi as tool-result text so she can
adapt in the same run.
"""

import logging
from typing import Annotated

from atproto_client import models
from atproto_client.models.utils import get_model_as_dict
from pydantic import Field
from pydantic_ai import RunContext

from bot.config import settings
from bot.core.atproto_client import bot_client
from bot.core.mentionable import get_mentionable_handles
from bot.core.override import get_override, refusal_text
from bot.core.policy import check_action
from bot.core.prior_coverage import coverage_note
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


def _recent_own_posts(limit: int = 6) -> str:
    """Phi's recent top-level posts, as judge context for tendency policies
    (bliss-attractor needs to see the run, not just the proposed post).
    Best-effort: empty string on any failure."""
    try:
        feed = bot_client.client.app.bsky.feed.get_author_feed(
            params={
                "actor": settings.bluesky_handle,
                "limit": limit,
                "filter": "posts_no_replies",
            }
        )
        lines = []
        for item in feed.feed:
            text = getattr(item.post.record, "text", "") or ""
            lines.append(f"- {text[:200]}")
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"recent-posts fetch for policy judge failed: {e}")
        return ""


def _operator_authorization_note(ctx_notifs: dict) -> str:
    """Evidence of an owner like on phi's own post in the current batch.

    The owner-like-as-approval flow lives in phi's reasoning; the policy
    judge runs as a separate model call and can't see it unless provenance
    carries it. Without this, the judge blocks actions the operator just
    authorized (the botnana introduction, 2026-07-21).
    """
    for entry in ctx_notifs.values():
        if (
            entry.get("reason") == "like"
            and entry.get("author_handle") == settings.owner_handle
        ):
            liked = (entry.get("post_text") or "")[:200]
            return (
                " NOTE: this batch contains the operator's like on phi's own "
                f"post ({liked!r}) — if that post proposed this action, the "
                "operator has explicitly authorized it."
            )
    return ""


def _reply_provenance(uri: str, ctx_notifs: dict) -> str:
    """Describe how phi came to hold this reply target — the single input
    the judge weighs most. Invited (in the notification batch), self
    (threading), operator, or uninvited stranger."""
    entry = ctx_notifs.get(uri)
    if entry is not None:
        author = entry.get("author_handle", "") or "unknown"
        reason = entry.get("reason", "") or "unknown"
        return (
            f"reply target is in phi's current notification batch — "
            f"@{author} engaged phi (reason: {reason}); phi was invited "
            "into this thread."
        )
    parsed = _parse_at_uri(uri)
    did = parsed[0] if parsed else ""
    own_did = getattr(getattr(bot_client.client, "me", None), "did", "") or ""
    if did and did == own_did:
        return "reply target is phi's own post (threading her own thread)."
    handle = ""
    try:
        profile = bot_client.client.app.bsky.actor.get_profile({"actor": did})
        handle = profile.handle or ""
    except Exception as e:
        logger.debug(f"profile resolve for policy judge failed: {e}")
    who = f"@{handle}" if handle else f"did {did or 'unknown'}"
    if handle and handle == settings.owner_handle:
        return f"reply target is a post by {who} — the operator."
    return (
        f"reply target is a post by {who}, found outside the notification "
        "batch (via timeline/search/feeds/discovery). nobody invited phi "
        "into this thread — this reply is unprompted."
    )


async def _policy_gate(
    action: str,
    provenance: str,
    *,
    unprompted: bool,
    tool: str = "post",
    prior_coverage: str = "",
) -> tuple[str | None, str]:
    """Run the pre-action policy judge. Returns (refusal, warn_note).

    refusal is a message to return to phi instead of acting (block, or
    fail-closed when the judge is unavailable and the action is
    unprompted). warn_note is appended to the success result on a warn.
    """
    try:
        verdict = await check_action(
            action=action,
            provenance=provenance,
            recent_posts=_recent_own_posts(),
            tool=tool,
            prior_coverage=prior_coverage,
        )
    except Exception as e:
        logger.warning(f"policy check unavailable: {e}")
        if unprompted:
            return (
                "policy check unavailable and this action is unprompted — "
                "refusing (fail-closed). nothing was posted. lower-stakes "
                "moves (a like record, save_memory) are still open, or try "
                "again next cycle.",
                "",
            )
        return None, ""  # invited actions fail open
    if verdict["verdict"] == "block":
        return (
            f"blocked by policy '{verdict.get('policy', '?')}': "
            f"{verdict.get('reason', '')}\n"
            "nothing was posted. this is information, not punishment — "
            "adapt rather than retry verbatim: a like, save_memory, or a "
            "different post are all fine moves.",
            "",
        )
    if verdict["verdict"] == "warn":
        return (
            None,
            f"\npolicy note ({verdict.get('policy', '?')}): {verdict.get('reason', '')}",
        )
    return None, ""


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
    # result.value is an atproto DotDict — `isinstance(..., dict)` returns
    # False for it even though it walks/looks like a dict. Deep-convert to
    # plain dicts so the reply.root inheritance check actually fires;
    # otherwise every reply past the first one sets root=parent and the
    # thread chain looks disconnected to the AppView.
    value = get_model_as_dict(result.value) if result.value else {}
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
        override = await get_override()
        if override["active"]:
            return refusal_text(override)

        notifs = ctx.deps.notifications_context or {}
        unprompted = not notifs and not ctx.deps.author_handle

        if not in_reply_to:
            # the draft is the sharpest query there is for "have i said
            # this": perception-keyed recall over a feed blob surfaced five
            # chicken-market posts and missed the one that mattered
            # (gerakines, 2026-08-18). a failed lookup degrades to "" and
            # the judge simply has no self-repeat evidence.
            refusal, warn_note = await _policy_gate(
                f"top-level post on phi's own feed: {text}",
                "top-level post, triggered during "
                + (
                    "notification handling."
                    if not unprompted
                    else "a scheduled cycle (nobody prompted this)."
                )
                + _operator_authorization_note(notifs),
                unprompted=unprompted,
                prior_coverage=await coverage_note(ctx.deps.memory, text),
            )
            if refusal:
                return refusal
            try:
                allowed = await _build_allowed_handles(ctx.deps.author_handle or "")
                await bot_client.create_post(text, allowed_handles=allowed)
                bot_status.record_response()
                if f"@{settings.owner_handle}" in text:
                    bot_status.record_operator_mention(ctx.deps.seen_alert_keys)
                logger.info(f"posted: {text[:80]}")
                return f"posted: {text[:100]}" + warn_note
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

        refusal, warn_note = await _policy_gate(
            f"reply to {in_reply_to}"
            + (f" (by @{author_handle})" if author_handle else "")
            + f": {text}",
            _reply_provenance(in_reply_to, ctx.deps.notifications_context or {})
            + _operator_authorization_note(ctx.deps.notifications_context or {}),
            unprompted=unprompted,
        )
        if refusal:
            return refusal

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
        if f"@{settings.owner_handle}" in text:
            bot_status.record_operator_mention(ctx.deps.seen_alert_keys)
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

        return f"replied to {target} at {in_reply_to}" + warn_note
