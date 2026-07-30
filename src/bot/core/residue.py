"""Residue — what recent runs left behind.

The system prompt is rebuilt from scratch every run; without this, nothing
connects one cognitive event to the next. Residue is a small, capacity-limited,
decaying set of items held as a public singleton record
(`io.zzstoatzz.phi.residue`) on phi's PDS.

The periphery writes, the workspace reads: at the end of each run a small
synth agent merges the run's summary into the buffer (carry / drop / add),
and the next run's [RESIDUE] block renders whatever survived. Capacity and
decay are enforced in code, not by the model — items expire RESIDUE_TTL_DAYS
after they were last held, and the buffer never exceeds RESIDUE_MAX_ITEMS.

Items are strictly DESCRIPTIVE — state of the world, never instructions.
This is the lesson of the curiosity queue (removed in bbf10c7): an ungated
store of self-assigned agenda items is standing pressure toward action that
nothing reviews. Residue records what happened and what remains unresolved;
whether to act on any of it is decided fresh, inside a run, under the usual
gates.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from bot.config import settings
from bot.core.atproto_client import BotClient
from bot.utils.time import relative_when

logger = logging.getLogger("bot.residue")

RESIDUE_COLLECTION = "io.zzstoatzz.phi.residue"
RESIDUE_MAX_ITEMS = 7
RESIDUE_TTL_DAYS = 3

_BLOCK_TTL_SECONDS = 60
_block_cache: dict = {"text": "", "fetched_at": 0.0}


def invalidate_residue_cache() -> None:
    _block_cache["text"] = ""
    _block_cache["fetched_at"] = 0.0


def prune(items: list[dict], now: datetime | None = None) -> list[dict]:
    """Drop expired items and enforce capacity (oldest lastHeldAt evicted)."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=RESIDUE_TTL_DAYS)
    alive = []
    for item in items:
        try:
            held = datetime.fromisoformat(item["lastHeldAt"].replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            continue
        if held.tzinfo is None:
            held = held.replace(tzinfo=UTC)
        if held >= cutoff:
            alive.append(item)
    alive.sort(key=lambda i: i["lastHeldAt"], reverse=True)
    return alive[:RESIDUE_MAX_ITEMS]


async def get_residue(client: BotClient) -> list[dict]:
    """Read the pruned residue buffer from phi's PDS. Empty list when unset."""
    await client.authenticate()
    if not client.client.me:
        return []
    try:
        response = client.client.com.atproto.repo.get_record(
            params={
                "repo": client.client.me.did,
                "collection": RESIDUE_COLLECTION,
                "rkey": "self",
            }
        )
    except Exception:
        return []
    value = dict(response.value) if response.value else {}
    items = value.get("items") or []
    return prune([dict(i) for i in items])


async def write_residue(client: BotClient, items: list[dict]) -> str:
    """Put the buffer record in place (history in the firehose). Returns AT-URI."""
    await client.authenticate()
    assert client.client.me is not None
    result = client.client.com.atproto.repo.put_record(
        data={
            "repo": client.client.me.did,
            "collection": RESIDUE_COLLECTION,
            "rkey": "self",
            "record": {
                "items": prune(items),
                "updatedAt": datetime.now(UTC).isoformat(),
            },
        }
    )
    invalidate_residue_cache()
    return result.uri


def _render(items: list[dict]) -> str:
    if not items:
        return ""
    lines = [
        "[RESIDUE — what recent runs left behind. descriptive state, not "
        "instructions: nothing here asks to be acted on, and it decays on its own.]"
    ]
    for item in items:
        first = relative_when(item.get("firstHeldAt", ""))
        last = relative_when(item.get("lastHeldAt", ""))
        age = f"held {first}, reinforced {last}" if first != last else f"held {first}"
        lines.append(f"- {item.get('content', '')} ({age})")
    return "\n".join(lines)


async def render_residue_block(client: BotClient) -> str:
    """Render the [RESIDUE] block. Cached briefly; empty when the buffer is."""
    now = time.time()
    if _block_cache["text"] and now - _block_cache["fetched_at"] < _BLOCK_TTL_SECONDS:
        return _block_cache["text"]
    try:
        block = _render(await get_residue(client))
    except Exception as e:
        logger.warning(f"residue block render failed: {e}")
        return ""
    _block_cache["text"] = block
    _block_cache["fetched_at"] = now
    return block


class ResidueUpdate(BaseModel):
    """The full next buffer, oldest concerns dropped, new ones added sparingly."""

    items: list[str] = Field(
        max_length=RESIDUE_MAX_ITEMS,
        description=(
            "the complete residue buffer after this run. repeat carried items "
            "VERBATIM (any rewording resets their age). empty list is valid."
        ),
    )


RESIDUE_SYNTH_SYSTEM_PROMPT = f"""\
You maintain the residue buffer for an autonomous agent: a few descriptive notes
about what its recent runs left behind. You are given the current buffer (with
ages) and a summary of what just happened in one run. Return the complete next
buffer.

Rules:
- Items are STRICTLY DESCRIPTIVE — a fact about what happened or what state
  something is in. NEVER an instruction, intention, plan, or suggestion.
  Write "alice's question about embeddings went unanswered", never "follow up
  with alice" or "should reply to alice". No imperative verbs, no "next",
  no "todo", no "remember to". If an item can only be phrased as something
  to do, it does not belong in the buffer.
- Carry forward items that still describe live state, repeated VERBATIM —
  character-for-character. Rewording an item resets its age.
- Drop items that no longer describe anything true or unresolved.
- Add at most 1-2 new items, and only when the run genuinely left something
  behind worth noting. Most runs add nothing. Returning the buffer unchanged
  is the expected common case.
- Items are one terse, concrete sentence in plain language. No first person,
  no stylistic flourish — this is a working note, not prose.
- Never exceed {RESIDUE_MAX_ITEMS} items; prefer fewer.
"""

_synth_agent: Agent[None, ResidueUpdate] | None = None


def get_residue_synth_agent() -> Agent[None, ResidueUpdate]:
    global _synth_agent
    if _synth_agent is None:
        _synth_agent = Agent[None, ResidueUpdate](
            name="phi-residue-synth",
            model=settings.extraction_model,
            output_type=ResidueUpdate,
            system_prompt=RESIDUE_SYNTH_SYSTEM_PROMPT,
        )
    agent = _synth_agent
    assert agent is not None
    return agent


def _merge(current: list[dict], next_contents: list[str], label: str) -> list[dict]:
    """Map the synth's contents back to items, carrying firstHeldAt on verbatim match."""
    now = datetime.now(UTC).isoformat()
    by_content = {i.get("content", ""): i for i in current}
    merged = []
    for content in next_contents:
        prior = by_content.get(content)
        merged.append(
            {
                "content": content,
                "firstHeldAt": prior["firstHeldAt"] if prior else now,
                "lastHeldAt": now,
                "source": prior.get("source", label) if prior else label,
            }
        )
    return prune(merged)


async def update_residue_from_run(client: BotClient, label: str, summary: str) -> None:
    """End-of-run synthesis: merge the run's outcome into the buffer.

    Best-effort — failures log and leave the buffer untouched. A carried item
    is a reinforced item (lastHeldAt bumps), so an "unchanged" buffer still
    writes; only empty-to-empty skips.
    """
    if not summary.strip():
        return
    current = await get_residue(client)
    buffer_lines = [
        f"- {i.get('content', '')} (held {relative_when(i.get('firstHeldAt', ''))})"
        for i in current
    ]
    prompt = (
        f"current buffer:\n{chr(10).join(buffer_lines) or '(empty)'}\n\n"
        f"run just finished ({label}). its summary:\n{summary}"
    )
    result = await get_residue_synth_agent().run(prompt)
    next_contents = result.output.items
    if not next_contents and not current:
        return
    merged = _merge(current, next_contents, label)
    await write_residue(client, merged)
    logger.info(f"residue buffer updated after {label}: {len(merged)} items")
