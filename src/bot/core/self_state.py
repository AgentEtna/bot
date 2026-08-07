"""[GOALS] and the recent-posting inventory — two separately-consumed organs.

GOALS are intent: what phi is for. Stored on PDS as canonical state.
`get_state_block` renders them alone — one block, one purpose.

The posting inventory is a structured, third-person tally of what phi's
recent top-level posts have covered (subjects / people / mode / missing
lately). Compiled by a small dedicated agent; written deliberately plain.
It is NOT phi's voice — exemplar pressure beats abstract rules, so the
inventory itself must stay out of phi's register or it teaches the bad
voice it was meant to describe. `get_inventory_block` renders it; the
[SELF] block composes it next to phi's own self record so testimony and
measurement sit in one place (they were two separately-named blocks
until 2026-08-07, which read as sprawl because it was).

The inventory pass is *derived* (not duplicated state) and cached in
memory: 1h TTL, invalidated when the latest post URI changes. The goals
compose is block-cached at 5min so notification polls (10s) don't
hammer PDS.
"""

import logging
import time
from datetime import UTC, datetime, timedelta

from pydantic_ai import Agent

from bot.config import settings
from bot.core.atproto_client import BotClient
from bot.core.goals import FIELD_CAPS
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
    """Force [GOALS] to recompose on the next read.

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


def _clamp(text: str, cap: int) -> str:
    """Visible truncation for field values written before the caps existed.
    New writes are rejected over-cap at the tool, so this marker is also
    the nudge to rewrite the field within its budget."""
    if len(text) <= cap:
        return text
    return text[: cap - 1] + f"… [cut at {cap} chars — rewrite this field tighter]"


def _format_goals_block(goals: list[dict]) -> str:
    if not goals:
        return ""
    lines = [
        "[GOALS — io.zzstoatzz.phi.goal. title/why/progress-means are "
        "owner-gated (propose_goal_change); current/next/last are yours "
        "to keep honest (update_goal_progress).]"
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
            clamped = _clamp(g["progress_signal"], FIELD_CAPS["progress_signal"])
            lines.append(f"  progress means (yours to revise): {clamped}")
        if g.get("current_state"):
            clamped = _clamp(g["current_state"], FIELD_CAPS["current_state"])
            lines.append(f"  current: {clamped}")
        if g.get("next_step"):
            lines.append(f"  next step: {_clamp(g['next_step'], FIELD_CAPS['next_step'])}")
        last_step = g.get("last_step")
        last_step_at = g.get("last_step_at", "")
        if last_step:
            age = relative_when(last_step_at)
            age_part = f"{age} — " if age else ""
            lines.append(
                f"  last step: {age_part}{_clamp(last_step, FIELD_CAPS['last_step'])}"
            )
        if g.get("blocked_by"):
            lines.append(f"  blocked: {g['blocked_by']}")
        stale = _stale_line(last_step_at)
        if stale:
            lines.append(stale)
    return "\n".join(lines)


async def get_state_block(
    client: BotClient, memory: NamespaceMemory | None = None
) -> str:
    """Compose [GOALS]. Cached 5min. `memory` is unused (kept for call
    compatibility; the live-computed friends line was deleted 2026-08-07 —
    it contradicted the goal's own phi-maintained `current` field)."""
    now = time.time()
    if _block_cache["text"] and now - _block_cache["fetched_at"] < _BLOCK_TTL_SECONDS:
        return _block_cache["text"]

    goals = await list_goal_records(client)
    block = _format_goals_block(goals)
    _block_cache["text"] = block
    _block_cache["fetched_at"] = now
    return block


async def get_inventory_block(client: BotClient) -> str:
    """The recent-posting inventory, rendered for composition inside
    [SELF]. Cached 1h, invalidated when the latest post URI changes."""
    now = time.time()
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
            return (
                "measured posting inventory (derived from your last 10 "
                "top-level posts; descriptive, not your voice — do not "
                "imitate its register):\n" + _inventory_cache["text"]
            )
    except Exception as e:
        logger.debug(f"posting inventory compose failed: {e}")
    return ""
