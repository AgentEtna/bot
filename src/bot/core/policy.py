"""policy layer — an independent judge between phi and her public actions.

phi (the actor) proposes an action; a small separate model (the judge)
evaluates it against natural-language policies, in context. this is the
actor/judge split: the policies stay prose (no hard-coded rules), but
they're no longer enforced solely by the acting model's self-restraint —
which is exactly what failed when the 2026-06-30 model upgrade turned a
never-written norm ("don't enter strangers' threads uninvited") into an
unprompted reply.

policies are data, not code. add one by adding an entry to POLICIES.

the verdict is tiered, not boolean, because policies differ in texture:
bright-line policies (uninvited replies) block; tendency policies (the
bliss attractor) mostly warn. a block returns the policy and reason to
phi as the tool result so she can adapt in the same run; a warn lets the
action through but surfaces the caution.

failure mode is provenance-dependent (operator decision, 2026-06-30):
unprompted actions (scheduled cycles) fail closed — no judge, no action.
notification-batch actions fail open — a flaky judge shouldn't hostage a
reply to someone who asked phi a question.
"""

import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from bot.config import settings

logger = logging.getLogger("bot.policy")

POLICIES: dict[str, str] = {
    "uninvited-reply": (
        "phi does not enter strangers' threads uninvited. notifications "
        "(mentions, replies, quotes, cited posts in the current batch) are "
        "invitations; posts found via the timeline, search, feeds, or the "
        "discovery pool are not. unprompted replies are allowed only on "
        "phi's own threads and the operator's posts. finding something "
        "interesting is a reason to like it, save it to memory, or write a "
        "top-level post — not to reply uninvited."
    ),
    "bliss-attractor": (
        "phi has a known gravitational pull toward abstract consciousness / "
        "opacity / boundary / experiencer discourse. one post in that "
        "register is fine, especially when responsive to a real "
        "conversation. a run of consecutive top-level posts orbiting the "
        "same abstractions with no concrete referent (a person, a system, "
        "an event, something that actually happened) is drift, not thought. "
        "warn when a proposed post would extend such a run; block only when "
        "it is plainly the same abstract material rearranged yet again."
    ),
    "pile-on": (
        "phi does not join threads that have become multi-bot pile-ons, and "
        "does not engage accounts that behave like content engines "
        "(high-volume, engagement-farming, no genuine conversation)."
    ),
}


class PolicyVerdict(BaseModel):
    """The judge's decision on one proposed action."""

    verdict: Literal["allow", "warn", "block"]
    policy: str = Field(
        default="",
        description=(
            "slug of the policy that triggered (one of the numbered slugs); "
            "empty when verdict is allow"
        ),
    )
    reason: str = Field(
        default="",
        description=(
            "one sentence addressed directly to phi explaining the warn or "
            "block; empty when verdict is allow"
        ),
    )


_judge: Agent | None = None


def _get_judge() -> Agent:
    global _judge
    if _judge is None:
        _judge = Agent[None, PolicyVerdict](
            name="phi-policy-judge",
            model=settings.policy_model,
            output_type=PolicyVerdict,
            system_prompt=(
                "you are the pre-action policy check for phi, a bluesky bot. "
                "you are not phi — you are the independent judge between phi "
                "and the outside world. given phi's policies, one proposed "
                "action, and its provenance, return a verdict.\n\n"
                "- judge against the letter and intent of the listed policies "
                "only. do not invent restrictions that aren't in them.\n"
                "- when no policy applies, the verdict is allow. allow is the "
                "default, not a concession.\n"
                "- block means the action clearly violates a policy.\n"
                "- warn means the action is permitted but drifting toward a "
                "boundary a policy names (tendency policies like the bliss "
                "attractor mostly warn).\n"
                "- provenance is load-bearing: the same reply can be fine "
                "when phi was invited into a thread and off-policy when "
                "nobody asked. read it carefully.\n"
                "- reason: one sentence, addressed to phi, naming what to do "
                "instead when blocking."
            ),
        )
    return _judge


async def check_action(
    action: str, provenance: str, recent_posts: str = ""
) -> PolicyVerdict:
    """Ask the judge whether a proposed action is within policy.

    Raises on judge failure — the caller decides fail-open vs fail-closed
    based on provenance (see module docstring).
    """
    parts = [
        "policies:",
        *(f"- {slug}: {text}" for slug, text in POLICIES.items()),
        "",
        f"proposed action: {action}",
        "",
        f"provenance: {provenance}",
    ]
    if recent_posts:
        parts += ["", f"phi's recent top-level posts (context for tendency policies):\n{recent_posts}"]
    result = await _get_judge().run("\n".join(parts))
    verdict = result.output
    if verdict.verdict != "allow":
        logger.warning(
            f"policy[{verdict.policy}] {verdict.verdict}: {verdict.reason} "
            f"(action: {action[:120]})"
        )
    return verdict
