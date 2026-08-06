"""Feed tools — graze feed CRUD, timeline reading, following."""

import logging
from typing import Annotated, Literal

from pydantic import Field
from pydantic_ai import RunContext

from bot.config import settings
from bot.core.atproto_client import bot_client
from bot.core.graze_client import GrazeClient
from bot.core.prior_coverage import coverage_note
from bot.tools._helpers import PhiDeps, _format_feed_posts, _is_owner

logger = logging.getLogger("bot.tools.feeds")


def register(agent, graze_client: GrazeClient):
    @agent.tool
    async def manage_feeds(
        ctx: RunContext[PhiDeps],
        action: Annotated[
            Literal["list", "create", "delete"],
            Field(description="list your graze feeds, create a new one, or delete one"),
        ],
        name: Annotated[
            str | None,
            Field(
                description=(
                    "[create] url-safe slug (e.g. 'electronic-music'); becomes "
                    "the feed rkey"
                )
            ),
        ] = None,
        display_name: Annotated[
            str | None, Field(description="[create] human-readable feed title")
        ] = None,
        description: Annotated[
            str | None, Field(description="[create] what the feed shows")
        ] = None,
        filter_manifest: Annotated[
            dict | None,
            Field(
                description=(
                    "[create] graze filter DSL (grazer engine operators). key "
                    "operators: regex_any: ['field', ['t1','t2']] (match any, "
                    "case-insensitive), regex_none (exclude), regex_matches "
                    "(single regex), and/or: [...filters]. field is usually "
                    "'text'. example: {'filter': {'and': [{'regex_any': "
                    "['text', ['jazz', 'bebop']]}]}}"
                )
            ),
        ] = None,
        algo_id: Annotated[
            int | None,
            Field(description="[delete] the numeric id shown by action='list'"),
        ] = None,
    ) -> str:
        """Manage your graze-powered bluesky feeds: list, create, or delete.

        Creating and deleting are owner-only. Deleting removes both the graze
        registration and the PDS feed generator record. To READ a feed's posts,
        use read_feed with the feed's name.
        """
        if action == "list":
            try:
                feeds = await graze_client.list_feeds()
                if not feeds:
                    return "no graze feeds found"
                lines = []
                for f in feeds:
                    display = f.get("display_name") or f.get("name") or "unnamed"
                    aid = f.get("id") or f.get("algo_id") or "?"
                    uri = f.get("feed_uri") or f.get("uri") or ""
                    rkey = f.get("record_name") or (
                        uri.rsplit("/", 1)[-1] if uri else "?"
                    )
                    lines.append(f"- {display} | name={rkey} | algo_id={aid}")
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"manage_feeds list failed: {e}")
                return f"failed to list feeds: {e}"

        if not _is_owner(ctx):
            return f"only @{settings.owner_handle} can {action} feeds"

        if action == "create":
            if not (name and display_name and description and filter_manifest):
                return (
                    "create needs name, display_name, description, and filter_manifest"
                )
            try:
                result = await graze_client.create_feed(
                    rkey=name,
                    display_name=display_name,
                    description=description,
                    filter_manifest=filter_manifest,
                )
                return f"feed created: {result['uri']} (algo_id={result['algo_id']})"
            except Exception as e:
                logger.warning(f"manage_feeds create failed: {e}")
                return f"failed to create feed: {e}"

        if algo_id is None:
            return "delete needs algo_id (see action='list')"
        try:
            # find the record_name from graze so we can delete the PDS record too
            feeds = await graze_client.list_feeds()
            record_name = None
            for f in feeds:
                if f.get("id") == algo_id:
                    record_name = f.get("record_name")
                    break

            await graze_client.delete_feed(algo_id)

            if record_name:
                assert bot_client.client.me is not None
                try:
                    bot_client.client.com.atproto.repo.delete_record(
                        data={
                            "repo": bot_client.client.me.did,
                            "collection": "app.bsky.feed.generator",
                            "rkey": record_name,
                        }
                    )
                except Exception as e:
                    logger.warning(f"PDS record delete failed: {e}")

            return f"deleted feed algo_id={algo_id}" + (
                f" and PDS record '{record_name}'" if record_name else ""
            )
        except Exception as e:
            logger.warning(f"manage_feeds delete failed: {e}")
            return f"failed to delete feed: {e}"

    @agent.tool
    async def read_feed(
        ctx: RunContext[PhiDeps], name: str = "timeline", limit: int = 20
    ) -> str:
        """Read posts from a feed.

        name: 'timeline' (default) for your following timeline — posts from
        accounts you follow; a saved feed name (e.g. 'for-you'); or one of
        your own feed slugs (see manage_feeds action='list').
        """
        try:
            if name == "timeline":
                response = await bot_client.get_timeline(limit=limit)
                if not response.feed:
                    return (
                        "your timeline is empty — you're not following anyone yet. "
                        f"ask @{settings.owner_handle} to have me follow some accounts!"
                    )
                result = _format_feed_posts(response.feed, limit=limit)
                # perception-keyed recall: seeing the material reminds you
                # that you already covered it.
                if note := await coverage_note(ctx.deps.memory, result):
                    result += f"\n\n{note}"
                return result

            # check saved feeds first (external feeds mapped by friendly name)
            feed_uri = settings.saved_feeds.get(name)
            if not feed_uri:
                # fall back to phi's own graze-powered feeds
                await bot_client.authenticate()
                assert bot_client.client.me is not None
                feed_uri = (
                    f"at://{bot_client.client.me.did}/app.bsky.feed.generator/{name}"
                )
            response = await bot_client.get_feed(feed_uri, limit=limit)
            if not response.feed:
                return "no posts in this feed yet"
            result = _format_feed_posts(response.feed, limit=limit)
            if note := await coverage_note(ctx.deps.memory, result):
                result += f"\n\n{note}"
            return result
        except Exception as e:
            return f"failed to read feed: {e}"

    @agent.tool
    async def follow_user(
        ctx: RunContext[PhiDeps], handle: str, subscribe_posts: bool = False
    ) -> str:
        """Follow a user on bluesky. Only the bot's owner can use this tool.

        Pass subscribe_posts=True to ALSO subscribe to the account's posts —
        their new top-level posts then arrive in your notifications instead of
        waiting for a timeline read. Right for official sources you must not
        miss (e.g. a market's exchange account); wrong for ordinary friends.
        """
        if not _is_owner(ctx):
            return f"only @{settings.owner_handle} can ask me to follow people"
        try:
            # check if already following
            already = False
            following = await bot_client.get_following()
            for f in following.follows:
                if f.handle == handle:
                    if not subscribe_posts:
                        return f"already following @{handle}"
                    already = True
                    break
            uri = await bot_client.follow_user(handle, subscribe_posts=subscribe_posts)
            base = f"now following @{handle}" if not already else f"@{handle}"
            sub = " + subscribed to their posts" if subscribe_posts else ""
            return f"{base}{sub} ({uri})"
        except Exception as e:
            return f"failed to follow @{handle}: {e}"
