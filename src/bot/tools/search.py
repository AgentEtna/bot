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
from bot.core.prior_coverage import coverage_note
from bot.tools._helpers import PhiDeps, _relative_age

# coral: the operator's firehose NER service (sibling repo). `/` returns its
# own endpoint list; the coral-editorial skill documents what each route is for.
CORAL_BASE = "https://coral.fly.dev"


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
            result = "\n\n".join(lines)
            # perception-keyed recall: seeing material triggers memory of
            # having covered it, before any decision to post is made.
            if note := await coverage_note(ctx.deps.memory, result):
                result += f"\n\n{note}"
            return result
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
        result = "\n".join(lines)
        if note := await coverage_note(ctx.deps.memory, result):
            result = f"{result}\n\n{note}"
        return result

    @agent.tool
    async def get_trending(ctx: RunContext[PhiDeps]) -> str:
        """Get what's currently trending on Bluesky. Returns coral's curated stories (named groups of co-occurring entities from the firehose), the entities driving them, and official Bluesky trending topics. Use this when someone asks about current events, what people are talking about, or when you want timely context. For anything deeper than this summary, use coral_query."""
        parts: list[str] = []

        async with httpx.AsyncClient(timeout=15) as client:
            # curated groups first: coral's LLM curator names clusters into
            # stories, which is denser signal than the bare entity list (and is
            # what phi's own editorialContext notes shape).
            try:
                r = await client.get(
                    f"{CORAL_BASE}/groups/history", params={"limit": 8}
                )
                r.raise_for_status()
                topics = r.json().get("topics", [])
                if topics:
                    lines = ["coral stories (curated from the firehose):"]
                    for t in topics:
                        members = ", ".join(t.get("entities", [])[:5])
                        lines.append(
                            f"  {t.get('label', '?')} — {members}"
                            f" (seen {t.get('observations', 0)}x)"
                        )
                    parts.append("\n".join(lines))
            except Exception as e:
                parts.append(f"coral stories unavailable: {e}")

            # entities second, and fewer of them: they catch a spike the curator
            # has not named yet, which is the one thing the groups cannot show.
            try:
                r = await client.get(f"{CORAL_BASE}/entity-graph")
                r.raise_for_status()
                data = r.json()
                entities = data.get("entities", [])
                stats = data.get("stats", {})

                by_trend = sorted(
                    entities, key=lambda e: e.get("trend", 0), reverse=True
                )[:8]

                lines = [
                    f"coral entities ({stats.get('active', 0)} active, "
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

    @agent.tool
    async def coral_query(
        ctx: RunContext[PhiDeps],
        path: Annotated[
            str,
            Field(
                description=(
                    "coral API path, e.g. '/groups/history?limit=20', "
                    "'/entity-graph', '/history/topics?hours=24', '/stats', "
                    "or the '/simcluster/...' mirror. GET '/' for the "
                    "endpoint list."
                )
            ),
        ],
    ) -> str:
        """Read any endpoint on coral, the operator's firehose entity-graph service. Use when get_trending's summary is not enough — to page further back through curated stories, pull an entity's history, or check graph health. Load the coral-editorial skill for what each route means."""
        if not path.startswith("/"):
            path = "/" + path
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(f"{CORAL_BASE}{path}")
                r.raise_for_status()
                body = r.text
        except Exception as e:
            return f"coral {path} failed: {e}"

        if len(body) > 8000:
            return (
                body[:8000]
                + f"\n\n[truncated at 8000 of {len(body)} chars — narrow the "
                "query with a limit/hours param]"
            )
        return body
