"""Incidents phi carries until she speaks to them.

The old shape dispatched a run whose *task prompt* carried the failures and
ordered a post. That made alerting a command rather than something phi
noticed, and it produced syslog with a mention attached. Now the facts
arrive as context and stay there until a post clears them, so choosing
silence is a choice she keeps facing instead of one that disappears.
"""

from bot.core.workflow_failures import (
    PENDING_MAX_AGE_SECONDS,
    PENDING_RENDER_LIMIT,
    add_pending,
    render_pending_block,
)

NOW = 1_800_000_000.0


def failure(run_id: str, name: str = "ingest", **kw) -> dict:
    return {
        "id": run_id,
        "name": name,
        "state_type": kw.get("state", "FAILED"),
        "state_message": kw.get("message", ""),
        "_incident": kw.get("incident", "new"),
        "_count": kw.get("count", 1),
    }


def test_a_failure_is_recorded_pending_not_delivered():
    pending = add_pending({}, [failure("a")], NOW)
    assert "a" in pending
    assert pending["a"]["first_seen_ts"] == NOW


def test_first_seen_survives_the_same_incident_reappearing():
    """Age is the pressure, so a repeat must not reset the clock."""
    pending = add_pending({}, [failure("a")], NOW)
    pending = add_pending(pending, [failure("a", count=5)], NOW + 3600)
    assert pending["a"]["first_seen_ts"] == NOW
    assert pending["a"]["count"] == 5


def test_unaddressed_incidents_age_out_after_a_day():
    """Pressure should build, not accumulate forever."""
    pending = add_pending({}, [failure("old")], NOW)
    fresh = add_pending(pending, [failure("new")], NOW + PENDING_MAX_AGE_SECONDS + 1)
    assert "old" not in fresh
    assert "new" in fresh


def test_block_reports_how_long_each_has_gone_unaddressed():
    pending = add_pending({}, [failure("a", name="leaflet-atlas")], NOW - 7200)
    block = render_pending_block(pending, NOW)
    assert "leaflet-atlas" in block
    assert "2h ago" in block
    assert "they stay here until you do" in block


def test_block_is_empty_when_nothing_is_pending():
    assert render_pending_block({}, NOW) == ""


def test_block_is_bounded():
    pending = {}
    for i in range(PENDING_RENDER_LIMIT + 5):
        pending = add_pending(pending, [failure(f"r{i}", name=f"flow-{i}")], NOW)
    block = render_pending_block(pending, NOW)
    assert block.count("run_id=") == PENDING_RENDER_LIMIT
    assert "and 5 more" in block


def test_the_exact_terminal_event_is_preserved_for_phi():
    """Inherited from render_failure_block, which this replaced: what phi is
    *given* stays exact. Only what she *says* was ever hers to choose, and
    the block labels the identifiers as reasoning material so she doesn't
    transcribe them into a post the operator reads in a phone app.
    """
    pending = add_pending(
        {},
        [failure("abc-123", name="bisk-snapshot-abc", message="invalid logs payload")],
        NOW,
    )
    block = render_pending_block(pending, NOW)
    assert "bisk-snapshot-abc" in block
    assert "FAILED" in block
    assert "run_id=abc-123" in block
    assert "invalid logs payload" in block
    assert "the operator reads bluesky, not a log" in block


def test_posting_clears_only_what_the_run_saw():
    """Clearing is structural: it keys off the incidents rendered into that
    run, so phi is never asked to self-report having addressed one."""
    from bot.status import BotStatus

    status = BotStatus()
    status.pending_incidents = add_pending({}, [failure("a"), failure("b")], NOW)
    status.clear_pending_incidents(["a"])
    assert set(status.pending_incidents) == {"b"}


def test_clearing_nothing_is_a_no_op():
    from bot.status import BotStatus

    status = BotStatus()
    status.pending_incidents = add_pending({}, [failure("a")], NOW)
    status.clear_pending_incidents([])
    assert set(status.pending_incidents) == {"a"}
