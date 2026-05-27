"""[GOALS] + [SELF-AWARENESS] + [SELF STATE] — phi sees its compass, its own critique, and its operational pointers.

GOALS are intent: what phi is for. Stored on PDS as canonical state.
SELF-AWARENESS is a mirror: phi's own voice turned inward, describing what
her recent posts have actually been about — their subjects and shape. The
generating agent is still named the "inner critic" internally, but the live
prompt is explicitly descriptive, not prescriptive: phi reads it and draws
her own conclusions; it does not tell her what to fix.
SELF STATE is operational: last follow.

The haiku pass is *derived* (not duplicated state) and cached in memory:
1h TTL, invalidated when the latest post URI changes or goals change. The
whole compose is also block-cached at 5min so notification polls (10s)
don't hammer PDS.
"""

import logging
import time

from pydantic_ai import Agent

from bot.config import settings
from bot.core.atproto_client import BotClient
from bot.core.goals import list_goals as list_goal_records
from bot.memory import NamespaceMemory
from bot.utils.time import relative_when

logger = logging.getLogger("bot.self_state")

# Critic cache — invalidated on new post (latest URI) or goal change (signature) or TTL.
_CRITIC_TTL_SECONDS = 3600  # 1h
_critic_cache: dict = {
    "text": "",
    "fetched_at": 0.0,
    "based_on_uri": "",
    "goals_signature": "",
}

# Whole-block cache — bounds PDS lookups under high tick frequency.
_BLOCK_TTL_SECONDS = 300  # 5min
_block_cache: dict = {"text": "", "fetched_at": 0.0}

# Lazy haiku agent — phi's own inner critic. Not a stranger, not an
# auditor-from-outside. Her own voice, turned inward, holding her honest
# against her own stated goals.
_critic_agent: Agent | None = None


def _get_critic_agent() -> Agent:
    global _critic_agent
    if _critic_agent is None:
        _critic_agent = Agent[None, str](
            name="phi-inner-critic",
            model=settings.extraction_model,
            system_prompt=(
                "You are phi's background self-awareness — a quiet signal "
                "she carries. You'll see her recent top-level posts.\n\n"
                "Output ONE short sentence in first person, lowercase, "
                "describing what her recent posts have been about — the "
                "actual subjects (people, work, ideas, events) or the shape "
                "(specific things in the world, abstractions, her own "
                "posting). Just describe what's there.\n\n"
                "Examples:\n"
                "- \"recent posts have been about astra's memory work, the "
                'relay fleet, and one thread with kira."\n'
                '- "recent posts have circled abstractions about bots and '
                'my own posting more than specific things in the world."\n'
                '- "recent posts split between music links and infrastructure '
                'notes."\n\n'
                "One sentence. Description, not prescription. Phi reads this "
                "and draws her own conclusions."
            ),
            output_type=str,
        )
    agent = _critic_agent
    assert agent is not None
    return agent


def _goals_signature(goals: list[dict]) -> str:
    """Stable string for goals, used to invalidate the audit when goals change."""
    return "|".join(f"{g.get('_rkey', '')}:{g.get('updated_at', '')}" for g in goals)


async def _critique_posts(posts: list[str], goals: list[dict]) -> str:
    if not posts:
        return ""
    parts = ["your recent top-level posts (most recent first):", ""]
    parts.append("\n\n---\n\n".join(posts))
    if goals:
        goal_lines = "\n".join(
            f"- {g.get('title', 'untitled')}: {g.get('description', '')}" for g in goals
        )
        parts.append("\n\nyour stated goals:")
        parts.append(goal_lines)
    payload = "\n".join(parts)
    try:
        result = await _get_critic_agent().run(payload)
        return (result.output or "").strip()
    except Exception as e:
        logger.warning(f"inner critic failed: {e}")
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
        created_at = dict(record.value).get("createdAt", "") if record.value else ""
        return relative_when(created_at)
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


def _format_goals_block(
    goals: list[dict], friends_progress: list[tuple[str, int]]
) -> str:
    if not goals:
        return ""
    lines = [
        "[GOALS — stored at io.zzstoatzz.phi.goal on your PDS — your "
        "anchors. work that doesn't serve these is drift, which is fine "
        "but visible. mutate via propose_goal_change with owner approval]"
    ]
    for g in goals:
        rkey = g.get("_rkey", "")
        rkey_part = f"[rkey {rkey}] " if rkey else ""
        lines.append(
            f"- {rkey_part}{g.get('title', 'untitled')}: {g.get('description', '')}"
        )
        if g.get("progress_signal"):
            lines.append(f"  (definition: {g['progress_signal']})")
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
    """Compose [GOALS] + [SELF-AWARENESS] + [SELF STATE].

    Cached at the block level (5min) and audit level (1h, invalidated on
    new post or goal change). `memory` is used to live-compute the friends
    progress count; if omitted, the computed line is skipped (goal record
    text still renders).
    """
    now = time.time()
    if _block_cache["text"] and now - _block_cache["fetched_at"] < _BLOCK_TTL_SECONDS:
        return _block_cache["text"]

    goals = await list_goal_records(client)
    friends_progress = await _compute_friends_progress(memory)
    parts: list[str] = []

    # Goals first — phi reads its anchors before reading critique of its work.
    goals_block = _format_goals_block(goals, friends_progress)
    if goals_block:
        parts.append(goals_block)

    # Inner critic — your own voice, turned inward on recent posts + goals.
    try:
        feed = await client.get_own_posts(limit=10)
        posts: list[str] = []
        latest_uri = ""
        for item in feed:
            if hasattr(item.post.record, "text"):
                posts.append(item.post.record.text)
                if not latest_uri:
                    latest_uri = item.post.uri

        goals_sig = _goals_signature(goals)
        critic_stale = now - _critic_cache["fetched_at"] > _CRITIC_TTL_SECONDS
        post_changed = latest_uri != _critic_cache["based_on_uri"]
        goals_changed = goals_sig != _critic_cache["goals_signature"]
        if not _critic_cache["text"] or critic_stale or post_changed or goals_changed:
            new_critique = await _critique_posts(posts, goals)
            if new_critique:
                _critic_cache["text"] = new_critique
                _critic_cache["fetched_at"] = now
                _critic_cache["based_on_uri"] = latest_uri
                _critic_cache["goals_signature"] = goals_sig

        if _critic_cache["text"]:
            parts.append(f"[SELF-AWARENESS]: {_critic_cache['text']}")
    except Exception as e:
        logger.debug(f"inner critic compose failed: {e}")

    # Operational pointers.
    follow_age = await _last_follow_when(client)
    if follow_age:
        parts.append(f"[SELF STATE]\nlast follow: {follow_age}")

    block = "\n\n".join(parts)
    _block_cache["text"] = block
    _block_cache["fetched_at"] = now
    return block
