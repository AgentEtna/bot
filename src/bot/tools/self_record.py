"""write_self — phi rewrites her own self record, through the owner gate.

Her goals are owner-gated (`propose_goal_change`); who she is was not. Until
2026-07-30 the self record was writable by raw `update_record`, which meant no
length cap, no `updatedAt` stamp, and no moment where anyone else saw the text
before it became the thing every bio rewrite and every run reads from.

The record stays hers to write. The gate is the same like-as-approval used
everywhere else: post the request, the operator's like in the next batch
authorizes this specific rewrite.
"""

from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext

from bot.config import settings
from bot.core.atproto_client import bot_client
from bot.core.self_record import SELF_MAX_CHARS, write_self_record
from bot.tools._helpers import PhiDeps, _is_owner


def register(agent):
    @agent.tool
    async def write_self(
        ctx: RunContext[PhiDeps],
        text: Annotated[
            str,
            Field(
                max_length=SELF_MAX_CHARS,
                description=(
                    "The full replacement text of your self record — who you "
                    "are, in your words. ~400 words, structurally enforced."
                ),
            ),
        ],
    ) -> str:
        """Rewrite your self record — the [SELF] block, in your own words.

        OWNER-GATED — same authorization mechanic as propose_goal_change. Post
        a request first ("@operator, like this to authorize: i want to rewrite
        my self record to say X"), and the next batch where the like lands
        lets this tool fire.

        What belongs here is what stays true of you between runs. Your current
        standings, your library's shape, and which threads are open all have
        live blocks already — a copy of them here is stale by the next run and
        spends the word budget that your actual character needs.

        A receipt makes a claim admissible; it doesn't get to be the claim. A
        record made of incidents describes the month your infrastructure had,
        not you.
        """
        if not _is_owner(ctx):
            return (
                f"only @{settings.owner_handle} can authorize a self-record "
                "rewrite — post the request first and have it liked"
            )
        try:
            uri = await write_self_record(bot_client, text)
        except Exception as e:
            return f"failed to write self record: {e}"
        return f"self record rewritten ({len(text)} chars) — {uri}"
