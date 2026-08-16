"""Goal tools — read anytime, mutate via the same owner-gate as follow_user."""

from typing import Annotated, Literal

from pydantic import Field
from pydantic_ai import RunContext

from bot.config import settings
from bot.core import goals
from bot.core.atproto_client import bot_client
from bot.core.self_state import invalidate_state_cache
from bot.tools._helpers import PhiDeps, _is_owner

FIELD_CAPS = goals.FIELD_CAPS


def register(agent):
    @agent.tool
    async def list_goals(ctx: RunContext[PhiDeps]) -> str:
        """List your current goals with rkeys.

        Use the rkey when proposing an update to an existing goal."""
        items = await goals.list_goals(bot_client)
        if not items:
            return "no goals set"
        lines: list[str] = []
        for g in items:
            kind = g.get("kind", "goal")
            lines.append(f"[rkey={g['_rkey']}] {g.get('title', 'untitled')} ({kind})")
            if g.get("description"):
                lines.append(f"  {g['description']}")
            if g.get("progress_signal"):
                lines.append(f"  progress = {g['progress_signal']}")
            if g.get("current_state"):
                lines.append(f"  current = {g['current_state']}")
            if g.get("next_step"):
                lines.append(f"  next = {g['next_step']}")
        return "\n".join(lines)

    @agent.tool
    async def propose_goal_change(
        ctx: RunContext[PhiDeps],
        title: Annotated[
            str,
            Field(description="Short goal title (e.g. 'make 3 friends')."),
        ],
        description: Annotated[
            str,
            Field(
                description=(
                    "What this goal concretely means — the work, the spirit, "
                    "the boundary. A stranger should be able to read it and "
                    "know what counts."
                )
            ),
        ],
        metabolism: Annotated[
            str,
            Field(
                description=(
                    "The goal's metabolism — name all four or the goal is a "
                    "wish: CLOCK (when attention fires — a schedule, a round, "
                    "a recurring moment), ARENA (where the world pushes back "
                    "— a market, a conversation, an index), ARTIFACT (what "
                    "you own and revise — a doctrine, a record, a shelf), "
                    "LEDGER (where results accrue publicly — P&L, blog "
                    "reports, thread history)."
                )
            ),
        ],
        kind: Annotated[
            Literal["goal", "interest"],
            Field(
                description=(
                    "'goal' (something you're for) or 'interest' (something "
                    "you're drawn to)."
                )
            ),
        ] = "goal",
        rkey: Annotated[
            str | None,
            Field(
                description=(
                    "Existing goal's rkey to update in place. Omit to create "
                    "a new goal. Get rkeys from list_goals."
                )
            ),
        ] = None,
    ) -> str:
        """Add or update one of your goals on PDS.

        OWNER-GATED — same authorization mechanic as follow_user. Post a
        request first ("@operator, like this to authorize: i want to add a
        goal for X"), and the next batch where the like lands will let this
        tool fire. Without an owner-like in the batch, this tool refuses.

        Goals are anchors — small set, evolved over time. Don't propose new
        goals casually; refine existing ones when the work has clarified.

        The operator gates DIRECTION (what you're for). The measure of
        progress is yours: set progress_signal via update_goal_progress,
        with receipts, and keep it reconciled with what you actually cite —
        a measure nobody's job is to refresh will rot (yours went stale for
        three months once and contradicted your own status lines)."""
        if not _is_owner(ctx):
            return (
                f"only @{settings.owner_handle} can change goals — "
                "post the authorization request first and have it liked"
            )
        try:
            uri = await goals.upsert_goal(
                bot_client, rkey, title, description, metabolism, kind
            )
            invalidate_state_cache()
            verb = "updated" if rkey else "added"
            return f"goal {verb}: '{title}' ({uri})"
        except Exception as e:
            verb = "update" if rkey else "add"
            return f"failed to {verb} goal: {e}"

    @agent.tool
    async def update_goal_progress(
        ctx: RunContext[PhiDeps],
        rkey: Annotated[
            str,
            Field(
                description=(
                    "rkey of the goal/interest to update — from list_goals or "
                    "the [GOALS AND INTERESTS] block."
                )
            ),
        ],
        current_state: Annotated[
            str,
            Field(description="where this goal actually stands right now, plainly."),
        ],
        next_step: Annotated[
            str,
            Field(description="the one concrete thing you could do next toward it."),
        ],
        last_step: Annotated[
            str,
            Field(
                description=(
                    "what you most recently did for this. repeat the prior "
                    "value if nothing new happened."
                )
            ),
        ],
        blocked_by: Annotated[
            str,
            Field(description="optional: why progress is stalled, if it is."),
        ] = "",
        evidence_uri: Annotated[
            str,
            Field(
                description=(
                    "optional AT-URI backing the last step (a post or reply you made)."
                )
            ),
        ] = "",
        progress_signal: Annotated[
            str | None,
            Field(
                description=(
                    "optional: revise what progress means AND where it "
                    "stands — the measure is yours, not the operator's. "
                    "state the honest number/claim with a receipt, and keep "
                    "it consistent with what you cite elsewhere."
                )
            ),
        ] = None,
    ) -> str:
        """Update your own progress on a goal or interest — NOT owner-gated.

        Use this to keep [GOALS AND INTERESTS] honest: record what you just
        did, where things stand, the next concrete step — and the measure
        itself (progress_signal is YOURS; a measure that contradicts your
        own status lines is an omission you're benefiting from). The
        constitutional fields (title / description / metabolism / kind)
        stay owner-gated via propose_goal_change.

        These fields are states and steps, not journals — hard length caps
        are enforced. Reasoning, doctrine, and per-position math belong in
        a greengale report or a memory, with the field carrying the one-line
        conclusion and a pointer."""
        over = [
            f"{name} is {len(value)} chars (max {cap})"
            for name, value, cap in (
                ("current_state", current_state, FIELD_CAPS["current_state"]),
                ("next_step", next_step, FIELD_CAPS["next_step"]),
                ("last_step", last_step, FIELD_CAPS["last_step"]),
                ("progress_signal", progress_signal or "", FIELD_CAPS["progress_signal"]),
            )
            if len(value) > cap
        ]
        if over:
            return (
                "not written — compress first: "
                + "; ".join(over)
                + ". state the conclusion in a sentence or two; put the "
                "reasoning in a greengale report or a memory and point to it."
            )
        try:
            evid = [evidence_uri] if evidence_uri else None
            uri = await goals.update_goal_progress(
                bot_client,
                rkey,
                current_state,
                next_step,
                last_step,
                blocked_by=blocked_by,
                evidence_uris=evid,
                progress_signal=progress_signal,
            )
            if uri is None:
                return (
                    f"unchanged — not written: {rkey} already holds this "
                    "current_state and next_step. your run summary is "
                    "recorded automatically; update the goal when its state "
                    "actually moves."
                )
            invalidate_state_cache()
            return f"progress updated for {rkey} ({uri})"
        except Exception as e:
            return f"failed to update progress: {e}"
