"""[RECENT FLOW MENTIONS] — what phi has recently posted about workflow state.

Hourly prefect_check has been re-tagging the operator about the same flow
breakage even when phi already raised it (and even when the underlying
issue resolved between checks). [RECENT OPERATIONS] caps at 10 PDS writes
across all collections, so on chatty days the prior tag-the-operator post
falls out of the window long before the next check.

This block pulls the last ~30 of phi's bsky posts, keeps the ones that
look like they were *about* workflow state (rebuild-atlas, prefect, work
pool, deployments, …), and renders them so phi can see at a glance what
she's already said. Combined with a sharper task prompt, this is meant to
collapse hourly re-tagging down to "tag once when something *new* breaks."
"""

from __future__ import annotations

import logging
from typing import TypedDict

from bot.core.atproto_client import BotClient
from bot.utils.time import relative_when

logger = logging.getLogger("bot.recent_flow_mentions")

# Substrings (case-insensitive) that mark a post as workflow-related.
# Conservative on purpose — false positives bloat the block, but false
# negatives mean phi re-tags. The keywords cover the deployments she
# tends to talk about (rebuild-atlas, ingest, transform, brief), the
# infrastructure terms she uses, and the operator-tag pattern.
WORKFLOW_KEYWORDS: tuple[str, ...] = (
    "rebuild-atlas",
    "atlas flow",
    "prefect",
    "deployment",
    "kubernetes-pool",
    "work pool",
    "worker pickup",
    "scheduled run",
    "flow run",
    "wrangler",
    "bun",
    "node v",
)

POST_FETCH_LIMIT = 30
RENDER_LIMIT = 12
TEXT_TRUNCATE = 200


class _Mention(TypedDict):
    rkey: str
    created_at: str
    text: str


def _short(text: str, n: int = TEXT_TRUNCATE) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def _looks_workflow_related(text: str) -> bool:
    """True if the post text mentions any workflow-related keyword."""
    lowered = (text or "").lower()
    return any(kw in lowered for kw in WORKFLOW_KEYWORDS)


async def get_recent_flow_mentions_block(client: BotClient) -> str:
    """Render the [RECENT FLOW MENTIONS] block for the prefect_check prompt.

    Empty string when phi has no relevant recent posts — caller can
    concatenate without worrying about blank sections.
    """
    try:
        await client.authenticate()
    except Exception:
        return ""
    if not client.client.me:
        return ""

    try:
        response = client.client.com.atproto.repo.list_records(
            {
                "repo": client.client.me.did,
                "collection": "app.bsky.feed.post",
                "limit": POST_FETCH_LIMIT,
            }
        )
    except Exception as e:
        logger.debug(f"recent flow mentions: list posts failed: {e}")
        return ""

    mentions: list[_Mention] = []
    for rec in response.records or []:
        value = dict(rec.value) if rec.value else {}
        text = value.get("text", "") or ""
        if not _looks_workflow_related(text):
            continue
        rkey = rec.uri.split("/")[-1]
        mentions.append(
            _Mention(
                rkey=rkey,
                created_at=str(value.get("createdAt", "") or ""),
                text=text,
            )
        )

    if not mentions:
        return ""

    mentions.sort(key=lambda m: m["rkey"], reverse=True)
    mentions = mentions[:RENDER_LIMIT]

    lines = [
        "[RECENT FLOW MENTIONS — what you've already posted about workflow "
        "state recently. if [WORKFLOW STATE] still shows the same item you've "
        "already raised here, the operator has already heard you. silence is "
        "the right answer unless something has *changed* (newly broken, newly "
        "recovered, or newly stuck) since your last mention.]"
    ]
    for m in mentions:
        when = relative_when(m["created_at"]) if m["created_at"] else ""
        when_part = f" ({when})" if when else ""
        lines.append(f"- you said{when_part}: {_short(m['text'])!r}")
    return "\n".join(lines)
