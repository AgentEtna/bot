"""Structural guard on the pdsx MCP toolset.

Posting flows through the trusted tools (bot.tools.posting) — that's where
the consent allowlist, the policy judge, and the operator override live. A
raw ``create_record``/``update_record`` into ``app.bsky.feed.*`` via pdsx
would bypass all three, which until 2026-06-30 was only a prompt rule.
This hook makes it structure: feed-collection writes through pdsx refuse
with a pointer to the trusted path. Every other pdsx capability — phi's
own custom collections, cosmik cards (her operator channel under an
override), profile records — passes through untouched.
"""

import logging
from typing import Any

logger = logging.getLogger("bot.mcp_guard")

_WRITE_TOOLS = {"create_record", "update_record"}
_BLOCKED_PREFIX = "app.bsky.feed."


async def guard_pdsx_tool_call(
    ctx: Any,
    call_tool: Any,
    name: str,
    tool_args: dict[str, Any],
) -> Any:
    """pydantic-ai ``process_tool_call`` hook for the pdsx MCP server."""
    if name in _WRITE_TOOLS:
        collection = str(tool_args.get("collection", ""))
        if collection.startswith(_BLOCKED_PREFIX):
            logger.warning(
                f"pdsx guard refused {name} into {collection} "
                f"(rkey={tool_args.get('rkey', '')!r})"
            )
            return (
                f"refused: raw {name} into {collection} bypasses your "
                "consent layer, policy check, and any operator override. "
                "posting, liking, and reposting flow through the trusted "
                "tools: post / like_post / repost_post."
            )
    return await call_tool(name, tool_args, None)
