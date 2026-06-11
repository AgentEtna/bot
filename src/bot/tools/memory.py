"""Memory tools — private search_memory (read) and save_memory (write)."""

from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext

from bot.tools._helpers import (
    PhiDeps,
    _format_unified_results,
    _format_user_results,
)


def register(agent):
    @agent.tool
    async def search_memory(
        ctx: RunContext[PhiDeps],
        query: Annotated[
            str, Field(description="what to look for in your private memory")
        ],
        about: Annotated[
            str,
            Field(
                description=(
                    "optional @handle to scope the search to one user's "
                    "namespace; empty searches your episodic notes plus the "
                    "current author's namespace together"
                )
            ),
        ] = "",
    ) -> str:
        """Search your private memory. Use to find past conversations and
        things you've explicitly saved.

        Without `about`: searches two places at once — your episodic notes
        (written via `save_memory`) and the current conversation author's
        namespace.

        With `about="@handle"`: searches that user's namespace only.

        For public network knowledge, use the semble tools instead.
        Write-side companion: `save_memory` (episodic notes)."""
        if not ctx.deps.memory:
            return "memory not available"

        if about.startswith("@"):
            handle = about.lstrip("@")
            results = await ctx.deps.memory.search(handle, query, top_k=10)
            if not results:
                return f"no memories found about @{handle}"
            return "\n".join(_format_user_results(results, handle))

        if about == "":
            results = await ctx.deps.memory.search_unified(
                ctx.deps.author_handle, query, top_k=8
            )
            if not results:
                return "no relevant memories found"
            return "\n".join(_format_unified_results(results, ctx.deps.author_handle))

        # bare handle without @
        results = await ctx.deps.memory.search(about, query, top_k=10)
        if not results:
            return f"no memories found about @{about}"
        return "\n".join(_format_user_results(results, about))

    @agent.tool
    async def save_memory(
        ctx: RunContext[PhiDeps],
        content: Annotated[
            str, Field(description="the memory to save, as a short statement")
        ],
        tags: Annotated[
            list[str],
            Field(description="0-3 lowercase topic tags to find it by later"),
        ],
        source_uri: Annotated[
            str,
            Field(
                description=(
                    "AT-URI of the post/thread/card this memory is grounded "
                    "in, when there is one — makes it checkable later"
                )
            ),
        ] = "",
    ) -> str:
        """Save something to your private memory for future semantic search.

        Writes to your private vector store (turbopuffer episodic namespace)
        — found later via `search_memory`, never surfaces back to you on its
        own.

        Pass source_uri when the memory is grounded in a specific post,
        thread, or card you can cite — it makes it checkable later. Empty
        is allowed when the thought is purely your own, but cite when you
        can.
        """
        if ctx.deps.memory:
            sources = [source_uri] if source_uri else None
            await ctx.deps.memory.store_episodic_memory(
                content, tags, source="tool", source_uris=sources
            )
            return f"saved to memory — {content[:100]}"
        return "private memory not available"
