"""The relay watch: coverage regressions are incidents, and event wakes
carry their content.

2026-08-18: zlay.waow.tech carried ~18% of the network for 16.5 hours,
unnoticed. No alert covered relay coverage, /_health returned 200 throughout,
and the only relay checks were the ones a conversation happened to prompt —
28 hours passed without one. These tests pin the machinery that replaces
phi's discretion with detection, and the deps plumbing that makes an event
wake as rich as a notification run.
"""

import inspect

from bot.core.alert_watch import QUIET_CLOSE_SECONDS, gate_scoped
from bot.core.relay_watch import (
    RELAY_NAMESPACE,
    is_relay_key,
    relay_states,
    wake_material,
)

T0 = 1_000_000.0


def _verdict(**overrides):
    """A trimmed /api/status verdict, numbers from the real 08-18 wedge."""
    base = {
        "window": {"runs": 24},
        "relays": [
            {
                "host": "zlay.waow.tech",
                "behind_lately": True,
                "behind_runs": 11,
                "runs": 24,
                "avg_coverage_pct": 66.78,
                "latest": {
                    "ts": "2026-08-18T18:06:00Z",
                    "coverage_pct": 18.91,
                    "behind": True,
                    "connected": True,
                },
            },
            {
                "host": "relay.waow.tech",
                "behind_lately": False,
                "behind_runs": 0,
                "runs": 24,
                "avg_coverage_pct": 95.87,
                "latest": {
                    "ts": "2026-08-18T18:06:00Z",
                    "coverage_pct": 98.0,
                    "behind": False,
                    "connected": True,
                },
            },
            # chronically-behind third-party relay: must never open incidents
            {
                "host": "atproto.africa",
                "behind_lately": True,
                "behind_runs": 24,
                "runs": 24,
                "avg_coverage_pct": 1.79,
                "latest": {
                    "ts": "2026-08-18T18:06:00Z",
                    "coverage_pct": 2.23,
                    "behind": True,
                    "connected": True,
                },
            },
        ],
    }
    return {**base, **overrides}


def test_behind_lately_watched_host_fires_with_the_numbers():
    states = {s["name"]: s for s in relay_states(_verdict())}
    zlay = states["zlay.waow.tech"]
    assert zlay["has_matches"]
    assert zlay["key"] == f"{RELAY_NAMESPACE}zlay.waow.tech"
    assert "11/24" in zlay["detail"]
    assert "67%" in zlay["detail"]
    assert "19%" in zlay["detail"]


def test_healthy_watched_host_is_present_but_quiet():
    """Non-firing states are what let gate_firings quiet-close a recovery."""
    states = {s["name"]: s for s in relay_states(_verdict())}
    assert not states["relay.waow.tech"]["has_matches"]
    assert states["relay.waow.tech"]["detail"] == ""


def test_unwatched_host_is_excluded_entirely():
    """atproto.africa is behind in 24/24 runs forever; watching the whole
    fleet would make that a permanent incident."""
    states = {s["name"]: s for s in relay_states(_verdict())}
    assert "atproto.africa" not in states


def test_disconnected_host_fires_even_when_not_behind_lately():
    verdict = _verdict()
    verdict["relays"][1]["latest"]["connected"] = False
    states = {s["name"]: s for s in relay_states(verdict)}
    assert states["relay.waow.tech"]["has_matches"]
    assert "DISCONNECTED" in states["relay.waow.tech"]["detail"]


# --- namespace isolation in the shared incident record ----------------------


def _logfire_inc():
    return {
        "phi:abc": {
            "opened_ts": T0 - 100,
            "last_seen_ts": T0 - 100,
            "count": 3,
            "name": "p95 over 3s",
            "project": "phi",
            "detail": "p95_ms=4039",
        }
    }


def test_relay_fold_leaves_logfire_incidents_untouched():
    """gate_firings prunes anything absent from its states; scoping is what
    stops the relay poll from quiet-closing the logfire slice."""
    incidents = _logfire_inc()
    cursor = {"phi:abc": "2026-08-18T00:00:00Z"}
    states = relay_states(_verdict())
    folded, folded_cursor = gate_scoped(
        states, incidents, cursor, T0 + QUIET_CLOSE_SECONDS + 1, scope=is_relay_key
    )
    assert folded["phi:abc"] == incidents["phi:abc"]
    assert "closed_ts" not in folded["phi:abc"]
    assert folded_cursor["phi:abc"] == "2026-08-18T00:00:00Z"
    assert f"{RELAY_NAMESPACE}zlay.waow.tech" in folded


def test_logfire_fold_leaves_relay_incidents_untouched():
    relay_key = f"{RELAY_NAMESPACE}zlay.waow.tech"
    incidents = {
        relay_key: {
            "opened_ts": T0,
            "last_seen_ts": T0,
            "count": 2,
            "name": "zlay.waow.tech",
            "project": "relay-eval",
            "detail": "behind the network in 11/24 recent eval runs",
        }
    }
    folded, _ = gate_scoped(
        [],  # a logfire snapshot with no alerts at all
        incidents,
        {relay_key: "2026-08-18T18:06:00Z"},
        T0 + QUIET_CLOSE_SECONDS + 1,
        scope=lambda k: not is_relay_key(k),
    )
    assert "closed_ts" not in folded[relay_key]


def test_recovery_quiet_closes_through_the_shared_machinery():
    states = relay_states(_verdict())
    incidents, cursor = gate_scoped(states, {}, {}, T0, scope=is_relay_key)
    zlay_key = f"{RELAY_NAMESPACE}zlay.waow.tech"
    assert "closed_ts" not in incidents[zlay_key]

    recovered = _verdict()
    recovered["relays"][0]["behind_lately"] = False
    incidents, cursor = gate_scoped(
        relay_states(recovered),
        incidents,
        cursor,
        T0 + QUIET_CLOSE_SECONDS + 1,
        scope=is_relay_key,
    )
    assert incidents[zlay_key]["closed_ts"]


def test_wake_material_names_the_host_and_the_numbers():
    """The wake string is what recall keys on — it must carry the host and
    the coverage facts, not a generic 'something fired'."""
    states = relay_states(_verdict())
    incidents, _ = gate_scoped(states, {}, {}, T0, scope=is_relay_key)
    material = wake_material(incidents, [f"{RELAY_NAMESPACE}zlay.waow.tech"])
    assert "zlay.waow.tech" in material
    assert "11/24" in material


# --- the event wake is as rich as a notification run ------------------------


def test_event_material_seeds_episodic_and_prior_coverage():
    """The 2026-08-18 gap in context form: a relay wake must key recall on
    the event (host, numbers) the way a batch keys it on post texts — not on
    the wake-up prose. Source-level assertion, the test_now_block idiom."""
    from bot.agent import PhiAgent

    src = inspect.getsource(PhiAgent.__init__)

    _, _, episodic = src.partition("async def inject_episodic")
    episodic = episodic.split("@_run_scoped")[0]
    assert "event_material or ctx.deps.run_prompt" in episodic, (
        "episodic recall must prefer the event's content over task prose"
    )

    _, _, coverage = src.partition("async def inject_prior_coverage")
    coverage = coverage.split("@_run_scoped")[0]
    assert "event_material" in coverage, (
        "prior coverage must key on event content when there is no batch"
    )
    assert "run_prompt" not in coverage, (
        "prior coverage must never query on task prose — instructions as a "
        "coverage query surface noise"
    )


def test_process_alerts_carries_material_into_deps():
    from bot.agent import PhiAgent

    src = inspect.getsource(PhiAgent.process_alerts)
    assert "event_material=material" in src, (
        "an alert wake must put the event's content on deps, or the run "
        "starts no richer than a clock slot"
    )
