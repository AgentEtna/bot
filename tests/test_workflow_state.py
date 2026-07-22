"""Regression tests for the workflow_state classifier.

The block previously used an LLM synth that hallucinated — given a clear
run history with three successes after a failure cluster, it labeled the
deployment "broken". The classifier is now pure Python; these tests
encode the behaviors that earlier failure must not recur with.
"""

from datetime import UTC, datetime, timedelta

from bot.core.workflow_state import _classify, _compose


def _iso(offset: timedelta) -> str:
    """ISO timestamp at NOW + offset (negative = past)."""
    return (datetime.now(UTC) + offset).isoformat().replace("+00:00", "Z")


def _run(*, state: str, end_offset_h: float, run_id: str = "x", msg: str = "") -> dict:
    end = _iso(timedelta(hours=-end_offset_h))
    start = _iso(timedelta(hours=-end_offset_h - 0.1))
    return {
        "id": run_id,
        "state_type": state,
        "state_message": msg,
        "start_time": start,
        "end_time": end,
        "deployment_id": "d1",
    }


def test_recent_success_after_failure_cluster_is_healthy():
    """The exact bug from 2026-05-04: 3 successes after 3 failures got labeled broken.

    Most-recent-terminal-state is the only ground truth for healthy vs broken.
    """
    # ordered most-recent first
    runs = [
        _run(state="COMPLETED", end_offset_h=2, run_id="loud-quoll"),
        _run(state="COMPLETED", end_offset_h=8, run_id="bizarre-vicugna"),
        _run(state="COMPLETED", end_offset_h=14, run_id="idealistic-groundhog"),
        _run(state="COMPLETED", end_offset_h=19, run_id="talented-tamarin"),
        _run(state="FAILED", end_offset_h=19.1, run_id="woodoo-reindeer"),
        _run(state="FAILED", end_offset_h=19.5, run_id="devout-kagu"),
        _run(state="FAILED", end_offset_h=19.6, run_id="rapid-roadrunner"),
    ]
    status, latest, qualifier = _classify(runs, stuck_ids=set())
    assert status == "healthy", latest
    assert latest.startswith("COMPLETED")
    assert qualifier == ""


def test_most_recent_failed_with_no_recovery_is_broken():
    runs = [
        _run(state="FAILED", end_offset_h=1, msg="boom"),
        _run(state="COMPLETED", end_offset_h=10),
    ]
    status, latest, qualifier = _classify(runs, stuck_ids=set())
    assert status == "broken"
    assert latest.startswith("FAILED")
    assert "boom" in qualifier


def test_stuck_outranks_terminal_state():
    runs = [
        {
            "id": "stuck-run",
            "state_type": "PENDING",
            "start_time": _iso(timedelta(hours=-2)),
            "expected_start_time": _iso(timedelta(hours=-2)),
            "deployment_id": "d1",
        },
        _run(state="COMPLETED", end_offset_h=10),
    ]
    status, latest, qualifier = _classify(runs, stuck_ids={"stuck-run"})
    assert status == "stuck"
    assert latest.startswith("PENDING")
    assert "not picked up" in qualifier


def test_stale_running_is_not_misdiagnosed_as_never_picked_up():
    runs = [
        {
            "id": "orphaned-run",
            "state_type": "RUNNING",
            "start_time": _iso(timedelta(hours=-2)),
            "expected_start_time": _iso(timedelta(hours=-2)),
            "deployment_id": "d1",
        },
        _run(state="COMPLETED", end_offset_h=10),
    ]
    status, latest, qualifier = _classify(runs, stuck_ids={"orphaned-run"})
    assert status == "stuck"
    assert latest.startswith("RUNNING")
    assert "orphaned run" in qualifier
    assert "not picked up" not in qualifier


def test_flapping_is_degraded_not_healthy():
    """Most-recent succeeded, but enough recent failures = degraded, not healthy."""
    runs = [
        _run(state="COMPLETED", end_offset_h=1, run_id="a"),
        _run(state="FAILED", end_offset_h=2, run_id="b"),
        _run(state="FAILED", end_offset_h=3, run_id="c"),
        _run(state="COMPLETED", end_offset_h=4, run_id="d"),
        _run(state="COMPLETED", end_offset_h=5, run_id="e"),
    ]
    status, latest, qualifier = _classify(runs, stuck_ids=set())
    assert status == "degraded"
    assert latest.startswith("COMPLETED")
    assert "recent terminals failed" in qualifier


def test_single_recent_failure_is_not_degraded_when_recovered():
    """One transient failure with most-recent COMPLETED stays healthy."""
    runs = [
        _run(state="COMPLETED", end_offset_h=1, run_id="a"),
        _run(state="FAILED", end_offset_h=2, run_id="b"),
        _run(state="COMPLETED", end_offset_h=3, run_id="c"),
        _run(state="COMPLETED", end_offset_h=4, run_id="d"),
    ]
    status, _, _ = _classify(runs, stuck_ids=set())
    assert status == "healthy"


def test_empty_runs_returns_empty_status():
    status, latest, qualifier = _classify([], stuck_ids=set())
    assert status == ""
    assert latest == ""
    assert qualifier == ""


def test_compose_orders_broken_before_healthy():
    runs = [
        # rebuild-atlas: most-recent is COMPLETED
        {
            **_run(state="COMPLETED", end_offset_h=2, run_id="quoll"),
            "deployment_id": "atlas",
        },
        # ingest: most-recent is FAILED
        {
            **_run(state="FAILED", end_offset_h=1, run_id="bad", msg="oops"),
            "deployment_id": "ingest",
        },
        {
            **_run(state="COMPLETED", end_offset_h=5, run_id="good"),
            "deployment_id": "ingest",
        },
    ]
    deployments = [
        {"id": "atlas", "name": "rebuild-atlas"},
        {"id": "ingest", "name": "ingest"},
    ]
    block = _compose({"runs": runs, "stuck": [], "deployments": deployments})
    # broken comes before healthy; FAILED token leads the ingest line, COMPLETED leads atlas
    assert block.find("- ingest: FAILED") < block.find("- rebuild-atlas: COMPLETED")
    assert "[broken — oops]" in block
    assert "[healthy]" in block


def test_compose_empty_when_no_data():
    assert _compose({"runs": [], "stuck": [], "deployments": []}) == ""
