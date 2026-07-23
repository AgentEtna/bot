"""Incident gating: repeated failures of one flow are one incident, not a
post per run (regression for the 2026-07-23 hourly ingest alert spam)."""

from bot.core.workflow_failures import (
    ESCALATION_SECONDS,
    QUIET_CLOSE_SECONDS,
    gate_alerts,
    incident_key,
)


def _run(name, run_id="r1", dep=None):
    return {"name": name, "id": run_id, "deployment_id": dep}


def test_incident_key_strips_run_suffix():
    assert incident_key(_run("ingest-c4c3b9a9")) == "flow:ingest"
    assert incident_key(_run("x", dep="d-1")) == "dep:d-1"


def test_first_failure_alerts_repeats_fold():
    t0 = 1000.0
    to_alert, inc = gate_alerts([_run("ingest-aaaaaaaa", "r1")], {}, t0)
    assert len(to_alert) == 1 and to_alert[0]["_incident"] == "new"

    # an hour later the same flow fails again — counted, not alerted
    to_alert2, inc2 = gate_alerts([_run("ingest-bbbbbbbb", "r2")], inc, t0 + 3600)
    assert to_alert2 == []
    assert inc2["flow:ingest"]["count"] == 2


def test_escalation_after_window():
    t0 = 1000.0
    _, inc = gate_alerts([_run("ingest-aaaaaaaa", "r1")], {}, t0)
    # keep it failing hourly, silently, until the escalation window passes
    t = t0
    for i in range(5):
        t += 3600
        alerts, inc = gate_alerts([_run(f"ingest-{i:08d}", f"r{i}")], inc, t)
        assert alerts == []
    t = t0 + ESCALATION_SECONDS
    alerts, inc = gate_alerts([_run("ingest-ffffffff", "rf")], inc, t)
    assert len(alerts) == 1
    assert alerts[0]["_incident"] == "ongoing"
    assert alerts[0]["_count"] >= 7


def test_incident_closes_after_quiet_period():
    t0 = 1000.0
    _, inc = gate_alerts([_run("ingest-aaaaaaaa", "r1")], {}, t0)
    t1 = t0 + QUIET_CLOSE_SECONDS + 1
    alerts, inc2 = gate_alerts([_run("ingest-bbbbbbbb", "r2")], inc, t1)
    assert len(alerts) == 1 and alerts[0]["_incident"] == "new"


def test_distinct_flows_are_distinct_incidents():
    t0 = 1000.0
    alerts, inc = gate_alerts(
        [_run("ingest-aaaaaaaa", "r1"), _run("brief-bbbbbbbb", "r2")], {}, t0
    )
    assert len(alerts) == 2
    assert set(inc) == {"flow:ingest", "flow:brief"}
