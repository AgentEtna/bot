import logging
from pathlib import Path

import httpx
from atproto import Client, Session, SessionEvent
from atproto_client import models

from bot.config import settings
from bot.core.ops_log import record_local_write
from bot.core.rich_text import create_facets

logger = logging.getLogger("bot.atproto_client")

SESSION_FILE = Path(".session")


def _get_session_string() -> str | None:
    """Load session from disk if it exists."""
    try:
        if SESSION_FILE.exists():
            return SESSION_FILE.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"failed to load session: {e}")
    return None


def _save_session_string(session_string: str) -> None:
    """Save session to disk."""
    try:
        SESSION_FILE.write_text(session_string, encoding="utf-8")
        logger.debug("session saved to disk")
    except Exception as e:
        logger.warning(f"failed to save session: {e}")


def _on_session_change(event: SessionEvent, session: Session) -> None:
    """Handle session changes (creation and refresh)."""
    if event in (SessionEvent.CREATE, SessionEvent.REFRESH):
        logger.debug(f"session {event.value}, saving to disk")
        _save_session_string(session.export())


MAX_GRAPHEMES = 300


def _split_text(text: str, max_len: int = MAX_GRAPHEMES) -> list[str]:
    """Split text into chunks that fit within bluesky's grapheme limit.

    Prefers splitting at paragraph breaks, then sentence boundaries, then word boundaries.
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # scan backwards from limit for best break point
        split_at = -1

        # prefer paragraph break (newline)
        for i in range(max_len - 1, max_len // 2, -1):
            if remaining[i] == "\n":
                split_at = i + 1
                break

        # then sentence boundary (.!?) followed by space or end
        if split_at < 0:
            for i in range(max_len - 1, max_len // 2, -1):
                if remaining[i] in ".!?" and (
                    i + 1 >= len(remaining) or remaining[i + 1] in " \n"
                ):
                    split_at = i + 1
                    break

        # then word boundary
        if split_at < 0:
            split_at = remaining.rfind(" ", 0, max_len)
            if split_at < max_len // 2:
                split_at = max_len  # hard break as last resort

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    return chunks


class BotClient:
    def __init__(self):
        self.client = Client(base_url=settings.bluesky_service)
        self.client.on_session_change(_on_session_change)
        self._authenticated = False

    async def authenticate(self):
        """Authenticate with Bluesky, reusing session if available."""
        if self._authenticated:
            return

        # Try to reuse existing session first
        session_string = _get_session_string()
        if session_string:
            try:
                logger.info("reusing saved session")
                self.client.login(session_string=session_string)
                self._authenticated = True
                logger.info("session restored")
                return
            except Exception as e:
                logger.warning(f"failed to reuse session: {e}, creating new one")
                # Delete invalid session file
                if SESSION_FILE.exists():
                    SESSION_FILE.unlink()

        # Create new session if no valid session exists
        logger.info("creating new session")
        self.client.login(settings.bluesky_handle, settings.bluesky_password)
        self._authenticated = True
        logger.info("new session created")

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    @property
    def me(self):
        """Get current user profile"""
        return self.client.me

    async def get_notifications(self, limit: int = 50):
        """Fetch unread notifications"""
        await self.authenticate()
        return self.client.app.bsky.notification.list_notifications(
            params={"limit": limit}
        )

    async def mark_notifications_seen(self, seen_at: str):
        """Mark notifications as seen up to a certain timestamp"""
        await self.authenticate()
        # Use the params format instead of data
        self.client.app.bsky.notification.update_seen({"seenAt": seen_at})

    async def create_post(
        self, text: str, reply_to=None, allowed_handles: set[str] | None = None
    ):
        """Create a new post or reply. Splits long text into a self-reply thread.

        *allowed_handles* controls which @mentions become notification-sending
        facets.  Pass the set of handles who consented to interaction (e.g.
        the message author + the bot owner).  ``None`` = no filtering (all
        mentions become facets, legacy behaviour for the ``post`` tool).
        """
        await self.authenticate()

        if len(text) <= 300:
            facets = create_facets(text, self.client, allowed_handles)
            if reply_to:
                result = self.client.send_post(
                    text=text, reply_to=reply_to, facets=facets
                )
            else:
                result = self.client.send_post(text=text, facets=facets)
            record_local_write(result.uri)
            return result

        chunks = _split_text(text)
        root_ref = reply_to.root if reply_to else None
        last_result = None

        for i, chunk in enumerate(chunks):
            facets = create_facets(chunk, self.client, allowed_handles)

            if i == 0:
                last_result = self.client.send_post(
                    text=chunk, reply_to=reply_to, facets=facets
                )
                if root_ref is None:
                    root_ref = models.ComAtprotoRepoStrongRef.Main(
                        uri=last_result.uri, cid=last_result.cid
                    )
            else:
                assert last_result is not None
                assert root_ref is not None
                parent_ref = models.ComAtprotoRepoStrongRef.Main(
                    uri=last_result.uri, cid=last_result.cid
                )
                thread_ref = models.AppBskyFeedPost.ReplyRef(
                    parent=parent_ref, root=root_ref
                )
                last_result = self.client.send_post(
                    text=chunk, reply_to=thread_ref, facets=facets
                )
            if last_result:
                record_local_write(last_result.uri)

        return last_result

    async def get_thread(self, uri: str, depth: int = 10):
        """Get a thread by URI"""
        await self.authenticate()
        return self.client.app.bsky.feed.get_post_thread(
            params={"uri": uri, "depth": depth}
        )

    async def get_posts(self, uris: list[str]):
        """Get multiple posts by URIs"""
        await self.authenticate()
        return self.client.app.bsky.feed.get_posts(params={"uris": uris})

    async def search_users(self, query: str, limit: int = 10):
        """Search for users"""
        await self.authenticate()
        return self.client.app.bsky.actor.search_actors(
            params={"q": query, "limit": limit}
        )

    async def like_post(self, uri: str, cid: str):
        """Like a post"""
        await self.authenticate()
        result = self.client.like(uri=uri, cid=cid)
        record_local_write(result.uri)
        return result

    async def repost(self, uri: str, cid: str):
        """Repost a post"""
        await self.authenticate()
        result = self.client.repost(uri=uri, cid=cid)
        record_local_write(result.uri)
        return result

    async def get_own_posts(self, limit: int = 10):
        """Fetch the bot's own recent posts (top-level only, no replies)."""
        await self.authenticate()
        response = self.client.app.bsky.feed.get_author_feed(
            params={
                "actor": self.client.me.did,
                "limit": limit,
                "filter": "posts_no_replies",
            }
        )
        return response.feed

    async def get_timeline(self, limit: int = 25):
        """Fetch the 'following' timeline feed."""
        await self.authenticate()
        return self.client.app.bsky.feed.get_timeline(params={"limit": limit})

    async def get_feed(self, feed_uri: str, limit: int = 25):
        """Fetch posts from a custom feed by AT-URI."""
        await self.authenticate()
        return self.client.app.bsky.feed.get_feed(
            params={"feed": feed_uri, "limit": limit}
        )

    async def follow_user(self, handle: str, subscribe_posts: bool = False) -> str:
        """Resolve handle to DID and create a follow record. Returns the record URI.

        With subscribe_posts, also sets a bsky activity subscription so the
        account's new posts arrive as notifications (requires the follow to
        exist first — bsky only allows subscribing to accounts you follow).
        """
        await self.authenticate()
        resolved = self.client.resolve_handle(handle)
        response = self.client.follow(resolved.did)
        record_local_write(response.uri)
        if subscribe_posts:
            # not in this atproto SDK version yet — raw XRPC procedure with
            # the session JWT, sent to our PDS (it proxies to the AppView)
            import httpx

            session = getattr(self.client, "_session", None)
            jwt = getattr(session, "access_jwt", None)
            pds = getattr(session, "pds_endpoint", None) or "https://bsky.social"
            if not jwt:
                raise RuntimeError("no session JWT for activity subscription")
            async with httpx.AsyncClient(timeout=15) as http:
                r = await http.post(
                    f"{pds}/xrpc/app.bsky.notification.putActivitySubscription",
                    headers={"Authorization": f"Bearer {jwt}"},
                    json={
                        "subject": resolved.did,
                        "activitySubscription": {"post": True, "reply": False},
                    },
                )
                r.raise_for_status()
        return response.uri

    async def get_following(self, limit: int = 100):
        """Get accounts the bot is following."""
        await self.authenticate()
        return self.client.app.bsky.graph.get_follows(
            params={"actor": self.client.me.did, "limit": limit}
        )


bot_client: BotClient = BotClient()


# --- self-identity block (cached) ---

_identity_block_cache: str | None = None


async def get_identity_block() -> str:
    """Build phi's self-identity block for the system prompt.

    Resolves the PDS endpoint from the DID document via the PLC directory
    on first call, then caches for the lifetime of the process. Phi's PDS
    doesn't change between deploys, so a process-lifetime cache is fine.

    The block prevents phi from confusing its own infrastructure with the
    operator's — a real failure mode caught when phi wrote a blog post
    claiming its memory lived on `pds.zzstoatzz.io` (the operator's PDS,
    not phi's).
    """
    global _identity_block_cache
    cached = _identity_block_cache
    if cached is not None:
        return cached

    await bot_client.authenticate()
    handle = settings.bluesky_handle
    me = bot_client.client.me
    did = me.did if me else "unknown"

    pds: str | None = None
    if did != "unknown":
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get(f"https://plc.directory/{did}")
                r.raise_for_status()
                doc = r.json()
                for svc in doc.get("service", []):
                    if svc.get("type") == "AtprotoPersonalDataServer":
                        pds = svc.get("serviceEndpoint")
                        break
        except Exception as e:
            logger.warning(f"failed to resolve pds endpoint for {did}: {e}")

    lines = [
        "[YOUR INFRASTRUCTURE]",
        f"handle: @{handle}",
        f"did: {did}",
    ]
    if pds:
        lines.append(f"pds: {pds}")
        lines.append(
            "this is YOUR pds, not the operator's. your records — observations, "
            "exchanges, blog posts, queue items — live here. the operator runs "
            "their own pds elsewhere; don't conflate them."
        )

    block = "\n".join(lines)
    _identity_block_cache = block
    logger.info(f"identity block built: {handle} / {did} / {pds or 'unknown pds'}")
    return block
