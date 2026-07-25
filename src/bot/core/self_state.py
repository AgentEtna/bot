"""[GOALS AND INTERESTS] + [SELF-AWARENESS] + [SELF STATE] — phi sees its compass, its recent posting inventory, and its operational pointers.

GOALS are intent: what phi is for. Stored on PDS as canonical state.
SELF-AWARENESS is a recent-posting inventory: a structured, third-person
tally of what phi's recent top-level posts have covered (subjects /
people / mode / missing lately). Compiled by a small dedicated agent;
written deliberately plain. The block is NOT phi's voice — exemplar
pressure beats abstract rules, so the inventory itself must stay out of
phi's register or it teaches the bad voice it was meant to describe.
SELF STATE is operational: last follow.

The inventory pass is *derived* (not duplicated state) and cached in
memory: 1h TTL, invalidated when the latest post URI changes. The whole
compose is also block-cached at 5min so notification polls (10s) don't
hammer PDS.
"""

import logging
import time
from datetime import UTC, datetime, timedelta

from atproto_client.models.utils import get_model_as_dict
from pydantic_ai import Agent

from bot.config import settings
from bot.core.atproto_client import BotClient
from bot.core.goals import list_goals as list_goal_records
from bot.memory import NamespaceMemory
from bot.utils.time import humanize_duration, relative_when

logger = logging.getLogger("bot.self_state")

# Recent-posting inventory cache — invalidated on new post (latest URI) or TTL.
_INVENTORY_TTL_SECONDS = 3600  # 1h
_inventory_cache: dict = {
    "text": "",
    "fetched_at": 0.0,
    "based_on_uri": "",
}

# Whole-block cache — bounds PDS lookups under high tick frequency.
_BLOCK_TTL_SECONDS = 300  # 5min
_block_cache: dict = {"text": "", "fetched_at": 0.0}


def invalidate_state_cache() -> None:
    """Force [GOALS AND INTERESTS] + [SELF-AWARENESS] + [SELF STATE] to recompose on the next read.

    Called by goal mutation tools (propose_goal_change, update_goal_progress)
    so phi doesn't see her own just-written progress as stale for up to 5min
    after a write.
    """
    _block_cache["text"] = ""
    _block_cache["fetched_at"] = 0.0


# Lazy haiku agent — compiles a recent-posting inventory. Deliberately
# not framed as a "voice" or "critic": its output is structured field /
# value pairs in plain English, third person, no first-person rhetoric.
# Why: this block is read every cycle as "what your recent posts have
# been about," and if it speaks in phi's register the model will
# reinforce that register as identity. Stay boring.
_inventory_agent: Agent | None = None


def _get_inventory_agent() -> Agent:
    global _inventory_agent
    if _inventory_agent is None:
        _inventory_agent = Agent[None, str](
            name="phi-posting-inventory",
            model=settings.extraction_model,
            system_prompt=(
                "You are a lab tech compiling a recent-posting inventory of "
                "phi's top-level posts. The output is a structured tally, not "
                "phi's voice — phi will read this as descriptive context, so "
                "any rhetoric here teaches her to imitate it.\n\n"
                "RULES:\n"
                "- third person, no first person.\n"
                "- no em-dashes.\n"
                "- no abstract noun phrases like 'structural questions about "
                "X,' 'the substrate of Y,' or 'X relocates Y.'\n"
                "- no rhetorical openings like 'recent posts have circled.'\n"
                "- no 'X isn't Y, it's Z' constructions.\n"
                "- prefer concrete words: actual subjects, actual handles, "
                "actual posting mode.\n\n"
                "OUTPUT exactly four lines, this format, no extra prose:\n\n"
                "subjects: <2-5 concrete topics, semicolons between>\n"
                "people: <@handles phi referenced by name, commas between, or 'none'>\n"
                "mode: <one short categorical phrase, e.g. 'mostly posts "
                "about tools and meetups', 'mostly replies about workflow', "
                "'reactive replies to specific posts', 'short observations'>\n"
                "missing lately: <2-4 categories absent that posts might "
                "reasonably include, semicolons between, e.g. 'jokes; "
                "concrete scenes; other people's specific work; music/art/feed "
                "discoveries'>\n\n"
                "Boring is correct. If a field has nothing to report, write 'none'."
            ),
            output_type=str,
        )
    agent = _inventory_agent
    assert agent is not None
    return agent


async def _compile_inventory(posts: list[str]) -> str:
    """Compile a third-person recent-posting inventory from phi's top-level posts."""
    if not posts:
        return ""
    payload = (
        "phi's recent top-level posts (most recent first):\n\n"
        + "\n\n---\n\n".join(posts)
    )
    try:
        result = await _get_inventory_agent().run(payload)
        return (result.output or "").strip()
    except Exception as e:
        logger.warning(f"posting inventory compile failed: {e}")
        return ""


async def _last_follow_when(client: BotClient) -> str:
    try:
        await client.authenticate()
        if not client.client.me:
            return ""
        response = client.client.com.atproto.repo.list_records(
            {
                "repo": client.client.me.did,
                "collection": "app.bsky.graph.follow",
                "limit": 1,
            }
        )
        if not response.records:
            return ""
        record = response.records[0]
        # get_model_as_dict, not dict(): a typed atproto Record serializes to
        # snake_case (`created_at`), so dict(...).get("createdAt") silently
        # returned "" and this block never rendered. see docs/patterns.md.
        value = get_model_as_dict(record.value) if record.value else {}
        return relative_when(value.get("createdAt", ""))
    except Exception as e:
        logger.debug(f"last_follow lookup failed: {e}")
        return ""


async def _compute_friends_progress(
    memory: NamespaceMemory | None,
) -> list[tuple[str, int]]:
    """Count handles with >=3 stored interactions in their namespace.

    Cheap check against the `make 3 friends` goal — the frozen text in the
    goal record says `currently: 0` but phi has in fact had substantive
    exchanges. This computes the objective portion of the goal's
    progress_signal live so phi reasons against current truth, not
    author-intent text.

    Excludes phi herself and the operator; both match the "non-operator"
    exclusion from the goal definition. The operator's testing account
    (devlog) is not excluded — phi can weigh it appropriately.

    Returns [(handle, exchange_count), ...] sorted by count desc.
    """
    if memory is None:
        return []
    try:
        user_prefix = f"{memory.NAMESPACES['users']}-"
        page = memory.client.namespaces(prefix=user_prefix)
    except Exception as e:
        logger.debug(f"friends-progress: listing namespaces failed: {e}")
        return []

    excluded = {settings.bluesky_handle, settings.owner_handle}
    results: list[tuple[str, int]] = []
    for ns_summary in page.namespaces:
        handle = ns_summary.id.removeprefix(user_prefix).replace("_", ".")
        if handle in excluded:
            continue
        try:
            user_ns = memory.client.namespace(ns_summary.id)
            # top_k=10 is enough to tell "has >=3"; we cap the meaningful
            # number at 10 exchanges anyway — more than that is just "a lot"
            response = user_ns.query(
                rank_by=("created_at", "desc"),
                top_k=10,
                filters={"kind": ["Eq", "interaction"]},
                include_attributes=["kind"],
            )
            count = len(response.rows) if response.rows else 0
            if count >= 3:
                results.append((handle, count))
        except Exception:
            continue

    results.sort(key=lambda t: (-t[1], t[0]))
    return results


# A goal/interest untouched for this long shows a "stalled" line — the
# salience that turns an inert anchor into something with visible pressure.
STALE_AFTER_DAYS = 4


def _stale_line(last_step_at: str) -> str:
    """One '  stalled: ...' line when a goal hasn't been advanced lately."""
    if not last_step_at:
        return "  stalled: no progress recorded yet"
    try:
        last = datetime.fromisoformat(last_step_at)
    except (ValueError, TypeError):
        return ""
    # Naive timestamps shouldn't reach here (upsert/update both write
    # tz-aware ISO), but mirror relative_when's defensive UTC backfill so a
    # legacy record never crashes prompt composition.
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    age = datetime.now(UTC) - last
    if age >= timedelta(days=STALE_AFTER_DAYS):
        return f"  stalled: no progress update in {humanize_duration(age)}"
    return ""


def _format_goals_block(
    goals: list[dict], friends_progress: list[tuple[str, int]]
) -> str:
    if not goals:
        return ""
    lines = [
        "[GOALS AND INTERESTS — stored at io.zzstoatzz.phi.goal on your PDS. "
        "goals are what you're for; interests are what you're drawn to. each "
        "carries a current state and a next step you can act on. title / why / "
        "progress-means are owner-gated (propose_goal_change); current / next "
        "step / last step are yours to keep honest (update_goal_progress).]"
    ]
    for g in goals:
        rkey = g.get("_rkey", "")
        rkey_part = f"[rkey {rkey}] " if rkey else ""
        kind = g.get("kind", "goal")
        lines.append(f"- {rkey_part}{g.get('title', 'untitled')} ({kind})")
        if g.get("description"):
            lines.append(f"  why: {g['description']}")
        if g.get("metabolism"):
            lines.append(f"  metabolism: {g['metabolism']}")
        if g.get("progress_signal"):
            lines.append(f"  progress means (yours to revise): {g['progress_signal']}")
        if g.get("current_state"):
            lines.append(f"  current: {g['current_state']}")
        if g.get("next_step"):
            lines.append(f"  next step: {g['next_step']}")
        last_step = g.get("last_step")
        last_step_at = g.get("last_step_at", "")
        if last_step:
            age = relative_when(last_step_at)
            age_part = f"{age} — " if age else ""
            lines.append(f"  last step: {age_part}{last_step}")
        if g.get("blocked_by"):
            lines.append(f"  blocked: {g['blocked_by']}")
        stale = _stale_line(last_step_at)
        if stale:
            lines.append(stale)
        # Live-computed friends progress, appended for the make-3-friends
        # goal. Identified heuristically by title; trivial to generalize
        # later to a per-goal computed-progress map if more goals accrue.
        if "friend" in g.get("title", "").lower() and friends_progress:
            qualifying = ", ".join(
                f"@{h} ({n}+)"[:100] for h, n in friends_progress[:8]
            )
            lines.append(
                f"  current (computed): {len(friends_progress)} handles "
                f"with ≥3 exchanges — {qualifying}"
            )
        elif "friend" in g.get("title", "").lower():
            lines.append("  current (computed): 0 handles with ≥3 exchanges")
    return "\n".join(lines)


async def get_state_block(
    client: BotClient, memory: NamespaceMemory | None = None
) -> str:
    """Compose [GOALS AND INTERESTS] + [SELF-AWARENESS] + [SELF STATE].

    Cached at the block level (5min) and inventory level (1h, invalidated
    on new post). `memory` is used to live-compute the friends progress
    count; if omitted, the computed line is skipped (goal record text
    still renders).
    """
    now = time.time()
    if _block_cache["text"] and now - _block_cache["fetched_at"] < _BLOCK_TTL_SECONDS:
        return _block_cache["text"]

    goals = await list_goal_records(client)
    friends_progress = await _compute_friends_progress(memory)
    parts: list[str] = []

    # Goals first — phi reads its anchors before reading anything else.
    goals_block = _format_goals_block(goals, friends_progress)
    if goals_block:
        parts.append(goals_block)

    # Recent-posting inventory — structured tally, deliberately not phi's voice.
    try:
        feed = await client.get_own_posts(limit=10)
        posts: list[str] = []
        latest_uri = ""
        for item in feed:
            if hasattr(item.post.record, "text"):
                posts.append(item.post.record.text)
                if not latest_uri:
                    latest_uri = item.post.uri

        cache_stale = now - _inventory_cache["fetched_at"] > _INVENTORY_TTL_SECONDS
        post_changed = latest_uri != _inventory_cache["based_on_uri"]
        if not _inventory_cache["text"] or cache_stale or post_changed:
            new_inventory = await _compile_inventory(posts)
            if new_inventory:
                _inventory_cache["text"] = new_inventory
                _inventory_cache["fetched_at"] = now
                _inventory_cache["based_on_uri"] = latest_uri

        if _inventory_cache["text"]:
            parts.append(
                "[SELF-AWARENESS — recent posting inventory; descriptive context, "
                "not your voice. do not imitate this block's register.]\n"
                + _inventory_cache["text"]
            )
    except Exception as e:
        logger.debug(f"posting inventory compose failed: {e}")

    # Operational pointers.
    follow_age = await _last_follow_when(client)
    if follow_age:
        parts.append(f"[SELF STATE]\nlast follow: {follow_age}")

    block = "\n\n".join(parts)
    _block_cache["text"] = block
    _block_cache["fetched_at"] = now
    return block
