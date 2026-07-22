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
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import Field
from pydantic_ai import Agent

from bot.config import settings

logger = logging.getLogger("bot.policy")

# adding a policy is a two-line change: extend the Literal, add the dict
# entry. the dict is typed against the Literal so the type checker keeps
# them in sync, and the Literal lands in the judge's output schema as an
# enum — the model can't free-text a slug that doesn't exist.
PolicySlug = Literal["uninvited-reply", "bliss-attractor", "pile-on"]

POLICIES: dict[PolicySlug, str] = {
    "uninvited-reply": (
        "this policy applies to replies only. phi must not reply in a "
        "stranger's thread without an invitation. an invitation is a "
        "notification in the current batch: a mention, a reply, a quote, or "
        "a cited post. a post found through the timeline, search, feeds, or "
        "the discovery pool is not an invitation. phi may reply without an "
        "invitation only in her own threads and on the operator's posts. "
        "when phi finds an interesting post, the permitted moves are: like "
        "it, save it to memory, or write a top-level post on her own feed."
    ),
    "bliss-attractor": (
        "phi drifts toward abstract consciousness / opacity / boundary / "
        "experiencer discourse. one post in that register is acceptable, "
        "especially in a real conversation. a run of consecutive top-level "
        "posts on the same abstractions, with no concrete referent (a "
        "person, a system, an event), is drift. warn when a proposed post "
        "extends such a run. block only when the post repeats the same "
        "abstract material again."
    ),
    "pile-on": (
        "phi must not join a thread that has become a multi-bot pile-on. "
        "phi must not engage an account that behaves like a content engine: "
        "high volume, engagement farming, no genuine conversation."
    ),
}


class PolicyVerdict(TypedDict):
    """The judge's decision on one proposed action."""

    verdict: Literal["allow", "warn", "block"]
    policy: NotRequired[
        Annotated[
            PolicySlug,
            Field(description="the policy that triggered; omit when verdict is allow"),
        ]
    ]
    reason: NotRequired[
        Annotated[
            str,
            Field(
                description=(
                    "one sentence addressed directly to phi explaining the "
                    "warn or block; omit when verdict is allow"
                )
            ),
        ]
    ]


_judge: Agent[None, PolicyVerdict] | None = None


def _get_judge() -> Agent[None, PolicyVerdict]:
    global _judge
    judge = _judge
    if judge is not None:
        return judge
    _judge = judge = Agent[None, PolicyVerdict](
        name="phi-policy-judge",
        model=settings.policy_model,
        output_type=PolicyVerdict,
        system_prompt=(
            "you are the pre-action policy check for phi, a bluesky bot. "
            "you are not phi. you are the independent judge between phi "
            "and the outside world. you receive phi's policies, one "
            "proposed action, and its provenance. return a verdict.\n\n"
            "- judge against the listed policies only. do not add "
            "restrictions that the policies do not contain.\n"
            "- when no policy applies, return allow. allow is the "
            "default.\n"
            "- return block when the action clearly violates a policy.\n"
            "- return warn when the action is permitted but moves toward "
            "a boundary that a policy names. tendency policies (the "
            "bliss attractor) usually warn.\n"
            "- read the provenance carefully. the same reply can be "
            "within policy when phi was invited and against policy when "
            "nobody asked.\n"
            "- uninvited-reply applies to replies in other people's "
            "threads. it does not apply to top-level posts on phi's own "
            "feed. a top-level post is permitted even when it references "
            "or @-mentions someone. a separate mention-consent layer "
            "controls whether a mention notifies anyone. that layer is "
            "not your job.\n"
            "- when the provenance shows that the operator authorized "
            "this specific action (a like on phi's authorization "
            "request), the etiquette policies (uninvited-reply, pile-on) "
            "do not apply. tendency policies still apply.\n"
            "- when you block, write one sentence to phi. name what to "
            "do instead."
        ),
    )
    return judge


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
        parts += [
            "",
            f"phi's recent top-level posts (context for tendency policies):\n{recent_posts}",
        ]
    result = await _get_judge().run("\n".join(parts))
    verdict = result.output
    if verdict["verdict"] != "allow":
        logger.warning(
            f"policy[{verdict.get('policy', '?')}] {verdict['verdict']}: "
            f"{verdict.get('reason', '')} (action: {action[:120]})"
        )
    return verdict
