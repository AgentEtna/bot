"""Incident math for the logfire alert watch (core/alert_watch.py)."""

from bot.core.alert_watch import (
    CLOSED_RETENTION_SECONDS,
    ESCALATION_SECONDS,
    QUIET_CLOSE_SECONDS,
    fold_firing,
    gate_firings,
    mark_mentioned,
    parse_webhook,
    render_alert_watch,
)

T0 = 1_000_000.0


def _state(**overrides):
    base = {
        "key": "pub-search:abc",
        "project": "pub-search",
        "name": "p95 over 3s",
        "active": True,
        "snoozed": False,
        "has_matches": True,
        "last_run": "2026-08-17T00:00:00Z",
        "detail": "p95_ms=4039 n=11",
    }
    return {**base, **overrides}


def test_firing_opens_incident():
    incidents, cursor = gate_firings([_state()], {}, {}, T0)
    inc = incidents["pub-search:abc"]
    assert inc["opened_ts"] == T0
    assert inc["count"] == 1
    assert inc["name"] == "p95 over 3s"
    assert cursor["pub-search:abc"] == "2026-08-17T00:00:00Z"


def test_reobserved_firing_is_not_news():
    incidents, cursor = gate_firings([_state()], {}, {}, T0)
    incidents, cursor = gate_firings([_state()], incidents, cursor, T0 + 300)
    inc = incidents["pub-search:abc"]
    assert inc["count"] == 1
    assert inc["last_seen_ts"] == T0 + 300


def test_new_last_run_advances_count():
    incidents, cursor = gate_firings([_state()], {}, {}, T0)
    incidents, cursor = gate_firings(
        [_state(last_run="2026-08-17T00:05:00Z")], incidents, cursor, T0 + 300
    )
    assert incidents["pub-search:abc"]["count"] == 2


def test_quiet_close_and_reopen():
    incidents, cursor = gate_firings([_state()], {}, {}, T0)
    quiet = _state(has_matches=False)
    t_close = T0 + QUIET_CLOSE_SECONDS
    incidents, cursor = gate_firings([quiet], incidents, cursor, t_close)
    assert incidents["pub-search:abc"]["closed_ts"] == t_close
    # a fresh firing after close is a new incident, not a resumed one
    incidents, cursor = gate_firings(
        [_state(last_run="2026-08-17T09:00:00Z")], incidents, cursor, t_close + 60
    )
    inc = incidents["pub-search:abc"]
    assert "closed_ts" not in inc
    assert inc["count"] == 1
    assert inc["opened_ts"] == t_close + 60


def test_not_firing_before_quiet_window_stays_open():
    incidents, cursor = gate_firings([_state()], {}, {}, T0)
    incidents, cursor = gate_firings(
        [_state(has_matches=False)], incidents, cursor, T0 + 60
    )
    assert "closed_ts" not in incidents["pub-search:abc"]


def test_closed_incident_pruned_after_retention():
    incidents = {
        "pub-search:abc": {
            "opened_ts": T0,
            "last_seen_ts": T0,
            "count": 3,
            "closed_ts": T0,
        }
    }
    incidents, _ = gate_firings([], incidents, {}, T0 + CLOSED_RETENTION_SECONDS)
    assert incidents == {}


def test_broken_alert_is_an_incident():
    """has_errors with no matches: the condition is unmonitored — news."""
    broken = _state(has_matches=False, has_errors=True, detail="ALERT BROKEN — x")
    incidents, _ = gate_firings([broken], {}, {}, T0)
    inc = incidents["pub-search:abc"]
    assert inc["count"] == 1
    assert "ALERT BROKEN" in inc["detail"]
    # repaired alert quiet-closes like any recovery
    fixed = _state(has_matches=False, has_errors=False)
    incidents, _ = gate_firings([fixed], incidents, {}, T0 + QUIET_CLOSE_SECONDS)
    assert incidents["pub-search:abc"]["closed_ts"] == T0 + QUIET_CLOSE_SECONDS


def test_snoozed_and_inactive_do_not_fire():
    incidents, _ = gate_firings(
        [_state(snoozed=True), _state(key="p:x", active=False)], {}, {}, T0
    )
    assert incidents == {}


def test_deleted_alert_incident_quiet_closes():
    incidents, cursor = gate_firings([_state()], {}, {}, T0)
    # the alert vanishes from the snapshot (deleted); before the quiet
    # window it stays open, after it it closes like any recovered alert
    incidents, cursor = gate_firings([], incidents, cursor, T0 + 60)
    assert "closed_ts" not in incidents["pub-search:abc"]
    t_close = T0 + QUIET_CLOSE_SECONDS
    incidents, cursor = gate_firings([], incidents, cursor, t_close)
    assert incidents["pub-search:abc"]["closed_ts"] == t_close


def test_cursor_pruned_to_live_alerts():
    _, cursor = gate_firings([_state()], {}, {"gone:alert": "old"}, T0)
    assert "gone:alert" not in cursor


def test_fold_firing_opens_once_then_counts():
    opened, incidents, cursor = fold_firing(_state(), {}, {}, T0)
    assert opened
    # every push is a real notify event, so counts advance even when
    # last_run hasn't changed (unlike a poll re-read)
    opened, incidents, cursor = fold_firing(_state(), incidents, cursor, T0 + 60)
    assert not opened
    assert incidents["pub-search:abc"]["count"] == 2


def test_fold_firing_reopens_closed_incident():
    incidents = {
        "pub-search:abc": {"opened_ts": T0, "last_seen_ts": T0, "count": 5,
                           "closed_ts": T0 + 100}
    }
    opened, incidents, _ = fold_firing(_state(), incidents, {}, T0 + 200)
    assert opened
    assert incidents["pub-search:abc"]["count"] == 1


def test_fold_firing_touches_nothing_else():
    incidents = {"other:key": {"opened_ts": T0, "last_seen_ts": T0, "count": 1}}
    _, out, cursor = fold_firing(_state(), incidents, {"other:key": "x"}, T0)
    assert out["other:key"]["count"] == 1
    assert "closed_ts" not in out["other:key"]
    assert cursor["other:key"] == "x"


def test_parse_webhook_real_payload():
    """The raw-data shape actually delivered on 2026-08-17."""
    state = parse_webhook(
        {
            "organization_name": "waow",
            "project_name": "phi",
            "alert_name": "canary",
            "timestamp": "2026-08-17T05:48:15.152674Z",
            "n_rows": 1,
            "data": [[739]],
            "columns": [{"name": "n", "type": {}, "nullable": False}],
            "errors": None,
            "links": {
                "alert": "https://logfire-us.pydantic.dev/waow/phi/alerts/"
                "07d2edad-867b-4d15-a730-2162d7be20e1?alertRunId=x"
            },
        }
    )
    assert state is not None
    # keyed by the UUID from links.alert so push and poll share incidents
    assert state["key"] == "phi:07d2edad-867b-4d15-a730-2162d7be20e1"
    assert state["has_matches"]
    assert state["detail"] == "n=739"
    assert state["last_run"] == "2026-08-17T05:48:15.152674Z"


def test_parse_webhook_missing_links_falls_back_to_name():
    state = parse_webhook({"alert_name": "p95", "project_name": "pub-search"})
    assert state is not None
    assert state["key"] == "pub-search:p95"


def test_parse_webhook_rejects_garbage():
    assert parse_webhook(None) is None
    assert parse_webhook("text") is None
    assert parse_webhook({"unrelated": 1}) is None


def test_render_empty_when_no_incidents():
    assert render_alert_watch({}, T0) == ""


def test_render_open_and_eligibility():
    incidents, cursor = gate_firings([_state()], {}, {}, T0)
    young = render_alert_watch(incidents, T0 + 60)
    assert "pub-search/p95 over 3s" in young
    assert "ESCALATION-ELIGIBLE" not in young.split("]", 1)[1]
    old = render_alert_watch(incidents, T0 + ESCALATION_SECONDS)
    assert "[ESCALATION-ELIGIBLE]" in old


def test_mention_disarms_then_rearms():
    """One tag per incident; a second only after another full window."""
    incidents, _ = gate_firings([_state()], {}, {}, T0)
    t_eligible = T0 + ESCALATION_SECONDS
    assert "[ESCALATION-ELIGIBLE]" in render_alert_watch(incidents, t_eligible)

    incidents = mark_mentioned(incidents, ["pub-search:abc"], t_eligible)
    just_after = render_alert_watch(incidents, t_eligible + 60)
    assert "operator notified" in just_after
    assert "do not mention them again" in just_after
    assert "ESCALATION-ELIGIBLE" not in just_after.split("]", 1)[1]

    rearmed = render_alert_watch(incidents, t_eligible + ESCALATION_SECONDS)
    assert "still firing long after the last mention" in rearmed


def test_mark_mentioned_skips_closed_and_missing():
    incidents = {"p:closed": {"opened_ts": T0, "closed_ts": T0 + 1}}
    out = mark_mentioned(incidents, ["p:closed", "p:missing"], T0 + 2)
    assert "mentioned_ts" not in out["p:closed"]
    assert "p:missing" not in out


def test_render_quieted_history():
    incidents = {
        "plyr-fm:def": {
            "opened_ts": T0,
            "last_seen_ts": T0,
            "count": 4,
            "name": "consumer silent",
            "project": "plyr-fm",
            "closed_ts": T0 + 100,
        }
    }
    out = render_alert_watch(incidents, T0 + 200)
    assert "quieted" in out
    assert "after 4 firings" in out
