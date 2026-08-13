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
        tag: Annotated[
            str,
            Field(
                description=(
                    "optional tag to filter episodic results by (e.g. "
                    "'correction' to audit what you've gotten wrong)"
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

        With `tag`: only episodic notes carrying that tag come back —
        `tag="correction"` is how you audit your own errata.

        For public network knowledge, use the semble tools instead.
        Write-side companion: `save_memory` (episodic notes)."""
        if not ctx.deps.memory:
            return "memory not available"

        if tag:
            results = await ctx.deps.memory.search_episodic(query, top_k=30)
            results = [r for r in results if tag in (r.get("tags") or [])][:10]
            if not results:
                return f"no memories tagged '{tag}' match that query"
            from bot.tools._helpers import _format_episodic_results

            return "\n".join(_format_episodic_results(results))

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
        """Save something to your private memory.

        Two ways it comes back: ambient recall (relevant notes ride into
        your context at the start of a run, keyed to what you're
        processing) and explicit `search_memory`.

        Re-saving a refined version of something you remember SUPERSEDES
        the old row — write-time reconciliation patches it with pedigree.
        This is how you edit your memory: save the better version, the
        stale one retires.

        Tag `correction` when recording that you got something wrong
        (claim, fix, and the post uri where you corrected it). Correction
        notes never fade from recall the way ordinary notes do. The
        corrected FACT belongs in your semble library only if it earns a
        place on its own — filed under its subject, never under the
        mistake.

        Pass source_uri when the memory is grounded in a specific post,
        thread, or card you can cite — it makes it checkable later.
        """
        if ctx.deps.memory:
            sources = [source_uri] if source_uri else None
            await ctx.deps.memory.store_episodic_memory(
                content, tags, source="tool", source_uris=sources
            )
            return f"saved to memory — {content[:100]}"
        return "private memory not available"
