"""Search tools — bluesky posts, trending, open web.

cosmik/semble network search lives in the semble MCP toolset
(semble_execute composing search_semantic and friends), not here.
"""

from datetime import date
from typing import Annotated, Literal

import httpx
from pydantic import Field
from pydantic_ai import RunContext

from bot.config import settings
from bot.core.atproto_client import bot_client
from bot.tools._helpers import PhiDeps, _relative_age


def register(agent):
    @agent.tool
    async def search_posts(
        ctx: RunContext[PhiDeps], query: str, limit: int = 10
    ) -> str:
        """Search Bluesky posts by keyword. Use this to find what people are saying about a topic."""
        try:
            response = bot_client.client.app.bsky.feed.search_posts(
                params={"q": query, "limit": min(limit, 25), "sort": "top"}
            )
            if not response.posts:
                return f"no posts found for '{query}'"

            today = date.today()
            lines = []
            for post in response.posts:
                text = post.record.text if hasattr(post.record, "text") else ""
                handle = post.author.handle
                likes = post.like_count or 0
                age = (
                    _relative_age(post.indexed_at, today)
                    if hasattr(post, "indexed_at") and post.indexed_at
                    else ""
                )
                age_str = f", {age}" if age else ""
                lines.append(f"@{handle} ({likes} likes{age_str}): {text[:200]}")
            return "\n\n".join(lines)
        except Exception as e:
            return f"search failed: {e}"

    @agent.tool
    async def web_search(
        ctx: RunContext[PhiDeps],
        query: Annotated[
            str,
            Field(description="Search query — natural language."),
        ],
        time_range: Annotated[
            Literal["day", "week", "month", "year"] | None,
            Field(
                description=(
                    "Bound results to a time window relative to today. "
                    "Use this BEFORE asserting recency in a post — "
                    "e.g. set 'week' before claiming something happened "
                    "this week. Without it, results may include stale items."
                )
            ),
        ] = None,
        topic: Annotated[
            Literal["general", "news"] | None,
            Field(
                description=(
                    "'news' optimizes for recent journalism, 'general' for "
                    "evergreen content. Default: general."
                )
            ),
        ] = None,
        max_results: Annotated[
            int,
            Field(description="How many results to return. Default 5."),
        ] = 5,
    ) -> str:
        """Search the open web via Tavily.

        Use to ground claims about the world outside atproto — current
        events, primary sources, official statements, technical docs.
        For atproto posts use search_posts; for the cosmik network use
        the semble tools (semble_execute with search_semantic).

        IMPORTANT: if you're about to assert something is recent, current,
        or 'this week,' pass time_range first. headlines without dates
        aren't evidence of when something happened."""
        if not settings.tavily_api_key:
            return "web_search unavailable: TAVILY_API_KEY not set"

        body: dict = {
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }
        if time_range:
            body["time_range"] = time_range
        if topic:
            body["topic"] = topic

        try:
            async with httpx.AsyncClient(timeout=20) as http:
                r = await http.post(
                    "https://api.tavily.com/search",
                    headers={
                        "Authorization": f"Bearer {settings.tavily_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            return f"web search failed: {e}"

        results = data.get("results", [])
        if not results:
            return f"no web results for '{query}'"

        scope_parts = []
        if time_range:
            scope_parts.append(f"time_range={time_range}")
        if topic:
            scope_parts.append(f"topic={topic}")
        scope = f" ({', '.join(scope_parts)})" if scope_parts else ""

        lines = [f"web results for '{query}'{scope}:"]
        for i, r_item in enumerate(results, 1):
            title = r_item.get("title", "untitled")
            url = r_item.get("url", "")
            content = (r_item.get("content") or "").strip()
            lines.append("")
            lines.append(f"{i}. {title}")
            if url:
                lines.append(f"   {url}")
            if content:
                lines.append(f"   {content[:400]}")
        return "\n".join(lines)

    @agent.tool
    async def get_trending(ctx: RunContext[PhiDeps]) -> str:
        """Get what's currently trending on Bluesky. Returns entity-level trends from the firehose (via coral) and official Bluesky trending topics. Use this when someone asks about current events, what people are talking about, or when you want timely context."""
        parts: list[str] = []

        async with httpx.AsyncClient(timeout=15) as client:
            # coral entity graph — NER-extracted trending entities from the firehose
            try:
                r = await client.get("https://coral.fly.dev/entity-graph")
                r.raise_for_status()
                data = r.json()
                entities = data.get("entities", [])
                stats = data.get("stats", {})

                by_trend = sorted(
                    entities, key=lambda e: e.get("trend", 0), reverse=True
                )[:15]

                lines = [
                    f"coral ({stats.get('active', 0)} active entities, "
                    f"{stats.get('clusters', 0)} clusters"
                    f"{', percolating' if stats.get('percolates') else ''}):"
                ]
                for e in by_trend:
                    lines.append(
                        f"  {e['text']} ({e.get('label', '')}) "
                        f"trend={e.get('trend', 0):.2f}"
                    )
                parts.append("\n".join(lines))
            except Exception as e:
                parts.append(f"coral unavailable: {e}")

            # official bluesky trending topics
            try:
                r = await client.get(
                    "https://public.api.bsky.app/xrpc/app.bsky.unspecced.getTrendingTopics"
                )
                r.raise_for_status()
                topics = r.json().get("topics", [])
                if topics:
                    lines = ["bluesky trending:"]
                    for t in topics[:15]:
                        lines.append(f"  {t.get('displayName', t.get('topic', ''))}")
                    parts.append("\n".join(lines))
            except Exception as e:
                parts.append(f"bluesky trending unavailable: {e}")

        return "\n\n".join(parts) if parts else "no trending data available"
