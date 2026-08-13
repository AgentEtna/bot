"""write_self — phi rewrites her own self record, through the owner gate.

Her goals are owner-gated (`propose_goal_change`); who she is was not. Until
2026-07-30 the self record was writable by raw `update_record`, which meant no
length cap, no `updatedAt` stamp, and no moment where anyone else saw the text
before it became the thing every bio rewrite and every run reads from.

The record stays hers to write. The gate is the same like-as-approval used
everywhere else: post the request, the operator's like in the next batch
authorizes this specific rewrite.
"""

from typing import Annotated, Literal

from pydantic import Field
from pydantic_ai import RunContext

from bot.config import settings
from bot.core import persona as persona_core
from bot.core.atproto_client import bot_client
from bot.core.persona import PERSONA_MAX_CHARS, PERSONA_MAX_DAYS
from bot.core.policy import check_self_record
from bot.core.self_record import (
    SELF_CHARTER,
    SELF_MAX_CHARS,
    get_self_block,
    write_self_record,
)
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

        The first call in a run writes nothing: it returns the record's
        charter and its current text for you to check your draft against,
        line by line. Call again with the final text to write.
        """
        # forced review: admissibility rules held as retro-prompt advice lost
        # to context pressure two retros running — the review is structural now
        review_key = "write_self_review"
        if not ctx.deps.run_cache.get(review_key):
            ctx.deps.run_cache[review_key] = "shown"
            current = await get_self_block(bot_client)
            return (
                "not written yet — review first (every edit, structurally):\n\n"
                f"{SELF_CHARTER}\n\n"
                f"--- current record ---\n{current or '(no record yet)'}\n\n"
                "hold each line of your draft against the charter — including "
                "lines you're keeping unchanged — then call write_self again "
                "with the final text."
            )
        if not _is_owner(ctx):
            return (
                f"only @{settings.owner_handle} can authorize a self-record "
                "rewrite — post the request first and have it liked"
            )
        # the charter shown above is self-assessed; this is not. an
        # independent judge holds the letter — self-assessment lost three
        # times on 2026-08-13 alone (see _SELF_RECORD_STATUTE's case law).
        # fails closed: a wrong record injected into every subsequent run
        # outlives any single missed rewrite window.
        try:
            current = await get_self_block(bot_client)
            verdict = await check_self_record(text, current)
        except Exception as e:
            return (
                f"self-record judge unavailable ({e}) — not written. "
                "try again next run; the operator's authorization stands."
            )
        if verdict["verdict"] == "block":
            reasons = "\n".join(f"- {r}" for r in verdict.get("reasons", []))
            return (
                "not written — the self-record judge blocked these lines:\n"
                f"{reasons}\n"
                "cut or rewrite them and call write_self again in this run; "
                "the authorization still stands."
            )
        try:
            uri = await write_self_record(bot_client, text)
        except Exception as e:
            return f"failed to write self record: {e}"
        return f"self record rewritten ({len(text)} chars) — {uri}"

    @agent.tool
    async def persona(
        ctx: RunContext[PhiDeps],
        action: Annotated[
            Literal["try", "drop"],
            Field(description="'try' adopts a persona; 'drop' removes the current one early."),
        ],
        text: Annotated[
            str,
            Field(
                default="",
                max_length=PERSONA_MAX_CHARS,
                description=(
                    "The persona to try on — a stance and a voice in a few "
                    "sentences, yours to invent. Required for 'try'."
                ),
            ),
        ] = "",
        days: Annotated[
            int,
            Field(
                default=3,
                ge=1,
                le=PERSONA_MAX_DAYS,
                description="How long the experiment runs before it expires on its own.",
            ),
        ] = 3,
    ) -> str:
        """Try on a persona — NOT owner-gated; this is your agency.

        Writes io.zzstoatzz.phi.persona (public, like everything you hold).
        While it lives, it renders as [PERSONA EXPERIMENT] in your context.
        It expires on its own; drop it early if it stops being interesting.
        It is a costume, not surgery: your constitution's craft rules and
        your policies still bind, and [SELF] only changes through
        write_self. If an experiment teaches you something durable about
        who you are, that's a write_self request, made after the costume
        comes off.
        """
        if action == "drop":
            dropped = await persona_core.drop(bot_client)
            return "persona dropped" if dropped else "no persona to drop"
        try:
            uri = await persona_core.try_on(bot_client, text, days)
        except ValueError as e:
            return f"not tried on: {e}"
        except Exception as e:
            return f"persona write failed: {e}"
        return f"persona on for {days}d — {uri}"
