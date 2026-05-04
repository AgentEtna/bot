"""Memory tools — private recall (read) and remember (write)."""

from pydantic_ai import RunContext

from bot.tools._helpers import (
    PhiDeps,
    _format_unified_results,
    _format_user_results,
)


def register(agent):
    @agent.tool
    async def recall(ctx: RunContext[PhiDeps], query: str, about: str = "") -> str:
        """Search your private memory. Use to find past conversations, things you've
        explicitly remembered, and observations that have aged out of your active pool.

        Without `about`: searches three places at once — your episodic notes
        (written via `remember`), the current conversation author's namespace,
        and the archive of observations that have aged out of [ACTIVE
        OBSERVATIONS] (your prior attention, no longer in the prompt).
        Archived observations are labeled `[archived observation: <reason>]`
        so you can tell prior-attention from a normal note.

        With `about="@handle"`: searches that user's namespace only.

        For public network knowledge, use search_network instead.
        Write-side companions: `remember` (episodic notes) and `observe`
        (active attention pool that re-surfaces in your prompt)."""
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
    async def remember(
        ctx: RunContext[PhiDeps],
        content: str,
        tags: list[str],
        source_uri: str = "",
    ) -> str:
        """Save something to your private memory for future semantic recall.

        Writes to your private vector store (turbopuffer episodic namespace)
        — searchable later via `recall`, never surfaces back to you on its
        own. Distinct from `observe`, which puts something in your bounded
        active-attention pool that re-surfaces in [ACTIVE OBSERVATIONS].

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
            return f"remembered — {content[:100]}"
        return "private memory not available"
