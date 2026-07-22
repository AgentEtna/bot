"""Tool registration for phi agent."""

from bot.core.graze_client import GrazeClient
from bot.tools._helpers import PhiDeps, _check_services_impl


def register_all(agent, graze_client: GrazeClient):
    """Register all tools on the agent."""
    from bot.tools import (
        atlas,
        bio,
        blog,
        bluesky,
        feeds,
        goals,
        images,
        media,
        memory,
        posting,
        search,
        topchicken,
        traces,
    )

    memory.register(agent)
    search.register(agent)
    feeds.register(agent, graze_client)
    bluesky.register(agent)
    bio.register(agent)
    blog.register(agent)
    goals.register(agent)
    images.register(agent)
    posting.register(agent)
    atlas.register(agent)
    media.register(agent)
    topchicken.register(agent)
    traces.register(agent)


__all__ = ["PhiDeps", "_check_services_impl", "register_all"]
