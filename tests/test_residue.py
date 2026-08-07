"""Tests for the residue buffer — decaying attention carried across runs.

The buffer is an io.zzstoatzz.phi.residue record on phi's PDS, rewritten at
the end of each run by a synth pass. Capacity and decay are enforced in
code: these tests pin the pruning, reinforcement, and merge semantics.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core import residue as residue_mod
from bot.core.residue import (
    RESIDUE_MAX_ITEMS,
    RESIDUE_TTL_DAYS,
    _merge,
    _render,
    prune,
    update_residue_from_run,
)


@pytest.fixture(autouse=True)
def _reset_residue_cache():
    residue_mod.invalidate_residue_cache()
    yield


def _item(content: str, first_days_ago: float = 0, last_days_ago: float = 0) -> dict:
    now = datetime.now(UTC)
    return {
        "content": content,
        "firstHeldAt": (now - timedelta(days=first_days_ago)).isoformat(),
        "lastHeldAt": (now - timedelta(days=last_days_ago)).isoformat(),
        "source": "batch processing",
    }


# --- prune: decay + capacity ---


def test_prune_drops_items_past_ttl():
    items = [
        _item("fresh"),
        _item("expired", first_days_ago=10, last_days_ago=RESIDUE_TTL_DAYS + 1),
    ]
    contents = [i["content"] for i in prune(items)]
    assert contents == ["fresh"]


def test_prune_keeps_old_item_recently_reinforced():
    items = [_item("old but alive", first_days_ago=30, last_days_ago=0.5)]
    assert len(prune(items)) == 1


def test_prune_evicts_oldest_held_when_over_capacity():
    items = [
        _item(f"item {i}", last_days_ago=i * 0.1) for i in range(RESIDUE_MAX_ITEMS + 3)
    ]
    kept = prune(items)
    assert len(kept) == RESIDUE_MAX_ITEMS
    assert "item 0" in [i["content"] for i in kept]
    assert f"item {RESIDUE_MAX_ITEMS + 2}" not in [i["content"] for i in kept]


def test_prune_drops_malformed_items():
    assert prune([{"content": "no timestamps"}, {"lastHeldAt": "not-a-date"}]) == []


# --- merge: reinforcement semantics ---


def test_verbatim_carry_preserves_first_held_and_bumps_last_held():
    current = [_item("open thread with alice", first_days_ago=2, last_days_ago=1)]
    merged = _merge(current, ["open thread with alice"], "cycle")
    assert merged[0]["firstHeldAt"] == current[0]["firstHeldAt"]
    assert merged[0]["lastHeldAt"] > current[0]["lastHeldAt"]
    assert merged[0]["source"] == "batch processing"  # original source kept


def test_reworded_item_gets_fresh_age_and_new_source():
    current = [_item("open thread with alice", first_days_ago=2)]
    merged = _merge(current, ["thread with alice, still open"], "cycle")
    assert merged[0]["firstHeldAt"] != current[0]["firstHeldAt"]
    assert merged[0]["source"] == "cycle"


# --- render ---


def test_render_empty_buffer_is_empty_string():
    assert _render([]) == ""


def test_render_shows_content_and_ages():
    block = _render([_item("watch the greengale thread", first_days_ago=2)])
    assert block.startswith("[RESIDUE")
    assert "watch the greengale thread" in block
    assert "held" in block


# --- update_residue_from_run ---


def _synth_returning(items: list[str]) -> MagicMock:
    agent = MagicMock()
    result = MagicMock()
    result.output.items = items
    agent.run = AsyncMock(return_value=result)
    return agent


async def test_update_skips_empty_summary_without_reading_or_writing():
    with (
        patch.object(residue_mod, "get_residue", AsyncMock()) as get,
        patch.object(residue_mod, "write_residue", AsyncMock()) as write,
    ):
        await update_residue_from_run(MagicMock(), "cycle", "   ")
    get.assert_not_awaited()
    write.assert_not_awaited()


async def test_update_skips_write_when_empty_stays_empty():
    with (
        patch.object(residue_mod, "get_residue", AsyncMock(return_value=[])),
        patch.object(
            residue_mod, "get_residue_synth_agent", lambda: _synth_returning([])
        ),
        patch.object(residue_mod, "write_residue", AsyncMock()) as write,
    ):
        await update_residue_from_run(MagicMock(), "cycle", "quiet run, nothing done")
    write.assert_not_awaited()


async def test_unchanged_buffer_still_writes_reinforcement():
    current = [_item("open thread with alice", last_days_ago=1)]
    with (
        patch.object(residue_mod, "get_residue", AsyncMock(return_value=current)),
        patch.object(
            residue_mod,
            "get_residue_synth_agent",
            lambda: _synth_returning(["open thread with alice"]),
        ),
        patch.object(residue_mod, "write_residue", AsyncMock()) as write,
    ):
        await update_residue_from_run(
            MagicMock(), "batch processing", "replied to alice"
        )
    write.assert_awaited_once()
    written = write.await_args.args[1]
    assert written[0]["lastHeldAt"] > current[0]["lastHeldAt"]


async def test_synth_failure_propagates_without_write():
    boom = MagicMock()
    boom.run = AsyncMock(side_effect=RuntimeError("model down"))
    with (
        patch.object(residue_mod, "get_residue", AsyncMock(return_value=[])),
        patch.object(residue_mod, "get_residue_synth_agent", lambda: boom),
        patch.object(residue_mod, "write_residue", AsyncMock()) as write,
        pytest.raises(RuntimeError),
    ):
        await update_residue_from_run(MagicMock(), "cycle", "did a thing")
    write.assert_not_awaited()


def test_strip_stamps_removes_accreted_age_annotations():
    """Prod items were found wearing eighteen '(held …)' stamps: the synth
    prompt put ages inside the item line, carry-VERBATIM copied them into
    content, and every run appended one more."""
    from bot.core.residue import strip_stamps

    polluted = (
        "Semble's connection write endpoint timed out three times."
        + " (held 17h ago)" * 18
    )
    assert strip_stamps(polluted) == (
        "Semble's connection write endpoint timed out three times."
    )
    assert strip_stamps("clean item (reinforced 2m ago)") == "clean item"
    assert strip_stamps("uses (parens) mid-sentence") == "uses (parens) mid-sentence"


def test_merge_matches_and_heals_stamped_content():
    """A stamped synth echo must still count as a verbatim carry (firstHeldAt
    preserved) and the stored content must come out clean."""
    from bot.core.residue import _merge

    current = [
        {
            "content": "the builder stalled at 35k documents (held 3h ago)",
            "firstHeldAt": "2026-08-06T00:00:00+00:00",
            "lastHeldAt": "2026-08-07T00:00:00+00:00",
            "source": "cycle",
        }
    ]
    merged = _merge(
        current,
        ["the builder stalled at 35k documents (held 3h ago) (held 5m ago)"],
        "cycle",
    )
    assert len(merged) == 1
    assert merged[0]["content"] == "the builder stalled at 35k documents"
    assert merged[0]["firstHeldAt"] == "2026-08-06T00:00:00+00:00"
