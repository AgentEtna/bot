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

import asyncio
import logging
import re
from typing import Any

import logfire

from bot.core.override import get_override, refusal_text
from bot.core.prior_coverage import coverage_note

logger = logging.getLogger("bot.mcp_guard")

# pdsx's three mutating verbs. `delete_record` was missing from this set
# until 2026-07-25, so a delete into any collection — including
# app.bsky.feed.post — passed the guard untouched. The destructive verb
# was the unchecked one.
_PDSX_MUTATIONS = {"create_record", "update_record", "delete_record"}
_BLOCKED_PREFIX = "app.bsky.feed."

# Collections whose trusted tool carries a gate that a raw record write would
# skip. The self record joined this on 2026-07-30: it is owner-gated through
# write_self, and it had been rewritten twice that day by raw update_record —
# unstamped and over the word cap — because nothing structural said otherwise.
_GATED_COLLECTIONS = {"io.zzstoatzz.phi.self": "write_self"}

# Verbs that only read. Anything else on a credentialed server is treated
# as a mutation — deny-by-default under an operator override, because the
# cost of over-gating a read is a retry and the cost of under-gating a
# write is a public action the operator asked not to happen.
_READ_VERBS = (
    "get",
    "list",
    "search",
    "describe",
    "read",
    "fetch",
    "query",
    "check",
    "whoami",
    "resolve",
    "inspect",
    "schema",
    "open",
)


def _bare_verb(name: str) -> str:
    """Strip pydantic-ai's ``tool_prefix`` so verbs compare across servers."""
    for prefix in ("pub_", "semble_", "tangled_", "prefect_", "lexidraw_"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _mutations(server: str, name: str, tool_args: dict[str, Any]) -> list[str]:
    """What this call would change. Empty means it only reads.

    semble is code-mode, so the mutation lives inside the submitted code
    rather than the tool name; everything else is named by its verb.
    """
    if server == "semble":
        return (
            _semble_writes(str(tool_args.get("code", "")))
            if name.endswith("execute")
            else []
        )
    if server == "pdsx":
        return (
            [f"{name} {tool_args.get('collection', '')}".strip()]
            if name in _PDSX_MUTATIONS
            else []
        )
    verb = _bare_verb(name)
    return [] if verb.startswith(_READ_VERBS) else [verb]


def _structural_refusal(
    server: str, name: str, tool_args: dict[str, Any]
) -> str | None:
    """Refusals that hold regardless of the override — writing a feed record
    by hand skips the consent allowlist and the policy judge, which no
    operator setting turns back on."""
    if server != "pdsx" or name not in _PDSX_MUTATIONS:
        return None
    collection = str(tool_args.get("collection", ""))
    if tool := _GATED_COLLECTIONS.get(collection):
        logger.warning(f"pdsx guard refused {name} into {collection}")
        return (
            f"refused: raw {name} into {collection} skips the owner gate, "
            f"the length cap, and the updatedAt stamp. use {tool} instead."
        )
    if not collection.startswith(_BLOCKED_PREFIX):
        return None
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


_SEMBLE_TOOL_RE = re.compile(r"\b(?:actors|cards|collections|connections)_[a-z_]+")
_SEMBLE_READ_VERBS = ("get", "list", "search", "describe")

# semble's backend has no unique constraint on collection names, and code-mode
# blocks do check-then-create — two execute calls running concurrently (the
# model batches parallel tool calls) raced on 2026-07-14 and created duplicate
# "Games"/"games" collections. serialize execute; reads stay concurrent.
_semble_execute_lock = asyncio.Lock()


def _semble_writes(code: str) -> list[str]:
    """Tool names in a code-mode block that mutate the library."""
    calls = set(_SEMBLE_TOOL_RE.findall(code))
    return sorted(
        c for c in calls if not c.split("_", 1)[1].startswith(_SEMBLE_READ_VERBS)
    )


_CORRECTABLE_SIGNATURES = (
    "validation error",
    "input should be",
    "literal_error",
    "field required",
    "missing",
    "invalidrequest",
    "unexpected keyword",
)


def _is_correctable(detail: str) -> bool:
    """Did semble reject the *arguments*, rather than being down?

    The two need different advice. A rejected argument is something phi
    can fix in the same run; an outage is not. Reporting both as an
    outage is how a lowercase `access_type` became "skip library writes
    this run" (2026-07-25).
    """
    low = detail.lower()
    return any(sig in low for sig in _CORRECTABLE_SIGNATURES)


def make_mcp_guard(server: str, run_label: str = ""):
    """One ``process_tool_call`` hook for every MCP server phi talks to.

    Three jobs, in order:

    1. **Structural refusal.** A raw feed-record write through pdsx skips
       the consent allowlist and the policy judge. No operator setting
       turns those back on, so this refuses regardless of override state.
    2. **The operator override.** Any call that would *change* something
       refuses while safe mode is active. This used to live only in
       `tools/posting.py` and `tools/topchicken.py`, which meant safe mode
       stopped phi posting to bluesky while leaving her free to write
       cosmik cards through semble and open issues on tangled under her
       own identity. Both are public actions in her name.
    3. **Provenance.** Every mutation leaves a logfire event with the run
       label, so what phi changed is queryable instead of reconstructed
       from PDS diffs afterwards.

    Reads pass straight through. Unrecognised verbs count as mutations —
    over-gating a read costs a retry, under-gating a write costs a public
    action the operator asked not to happen.
    """

    async def process(
        ctx: Any,
        call_tool: Any,
        name: str,
        tool_args: dict[str, Any],
    ) -> Any:
        if refusal := _structural_refusal(server, name, tool_args):
            return refusal

        changes = _mutations(server, name, tool_args)
        if changes:
            override = await get_override()
            if override["active"]:
                logger.warning(f"override refused {server}.{name}: {changes}")
                return refusal_text(override)
            logfire.info(
                "{server} mutation during {run_label}: {changes}",
                server=server,
                run_label=run_label,
                changes=changes,
            )

        # semble's code-mode server is single-flight: concurrent execute
        # calls race on its side.
        if server == "semble" and name.endswith("execute"):
            async with _semble_execute_lock:
                result = await _invoke(call_tool, server, name, tool_args, run_label)
        else:
            result = await _invoke(call_tool, server, name, tool_args, run_label)
        return await _with_coverage(ctx, result)

    return process


_COVERAGE_MIN_CHARS = 400


async def _with_coverage(ctx: Any, result: Any) -> Any:
    """Perception-keyed recall on every MCP result, structurally.

    Any sizeable textual result is material entering phi's context, so her
    own posts nearest it ride along — the same recall feeds/search carry
    inline, without per-tool wiring. This seam exists because a pdsx
    list_records call fed her the plyr catalog with no recall attached and
    she "discovered" it three runs straight. Recall going quiet must never
    break the call itself.
    """
    memory = getattr(getattr(ctx, "deps", None), "memory", None)
    if not memory or not isinstance(result, str) or len(result) < _COVERAGE_MIN_CHARS:
        return result
    note = await coverage_note(memory, result)
    return f"{result}\n\n{note}" if note else result


async def _invoke(
    call_tool: Any, server: str, name: str, tool_args: dict[str, Any], run_label: str
) -> Any:
    """Call the tool, turning failures into something phi can act on."""
    try:
        return await call_tool(name, tool_args, None)
    except Exception as e:
        logger.warning(f"{server} {name} failed during {run_label}: {e}")
        detail = str(e)
        if _is_correctable(detail):
            # a rejected argument is not an outage. semble told phi
            # `Input should be 'OPEN' or 'CLOSED'` and this wrapper
            # relabelled it "unavailable, skip library writes" — throwing
            # away the one thing that would have fixed the call.
            return (
                f"{server} rejected those arguments ({detail[:400]}). "
                "this is fixable from here — check the shape and call it again."
            )
        return (
            f"{server} is unavailable right now ({type(e).__name__}: "
            f"{detail[:300]}). skip that write this run and continue with the "
            "rest of the task — mention the outage in your summary so the "
            "operator sees it."
        )
