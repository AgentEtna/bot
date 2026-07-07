"""process_tool_call hooks for phi's MCP toolsets.

Two hooks live here: a structural guard on pdsx (refuses feed writes) and
an observational logger on semble (records library-write provenance).

pdsx guard: posting flows through the trusted tools (bot.tools.posting) —

that's where
the consent allowlist, the policy judge, and the operator override live. A
raw ``create_record``/``update_record`` into ``app.bsky.feed.*`` via pdsx
would bypass all three, which until 2026-06-30 was only a prompt rule.
This hook makes it structure: feed-collection writes through pdsx refuse
with a pointer to the trusted path. Every other pdsx capability — phi's
own custom collections, cosmik cards (her operator channel under an
override), profile records — passes through untouched.
"""

import logging
import re
from typing import Any

import logfire

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


_SEMBLE_TOOL_RE = re.compile(r"\b(?:actors|cards|collections|connections)_[a-z_]+")
_SEMBLE_READ_VERBS = ("get", "list", "search", "describe")


def _semble_writes(code: str) -> list[str]:
    """Tool names in a code-mode block that mutate the library."""
    calls = set(_SEMBLE_TOOL_RE.findall(code))
    return sorted(
        c for c in calls if not c.split("_", 1)[1].startswith(_SEMBLE_READ_VERBS)
    )


def make_semble_write_logger(run_label: str):
    """pydantic-ai ``process_tool_call`` hook for the semble MCP server.

    Purely observational — semble writes are allowed (live authoring is the
    primary path to phi's public library), but each one leaves a logfire
    event carrying the run label and the executed code, so provenance is
    queryable instead of reconstructed from PDS diffs.
    """

    async def process(
        ctx: Any,
        call_tool: Any,
        name: str,
        tool_args: dict[str, Any],
    ) -> Any:
        if name == "execute":
            code = str(tool_args.get("code", ""))
            writes = _semble_writes(code)
            if writes:
                logfire.info(
                    "semble write during {run_label}: {writes}",
                    run_label=run_label,
                    writes=writes,
                    code=code[:2000],
                )
        return await call_tool(name, tool_args, None)

    return process
