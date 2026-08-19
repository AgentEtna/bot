"""phi watches the operator's relays the way she watches their alerts.

On 2026-08-18 zlay.waow.tech carried ~18% of the network for 16.5 hours and
nobody was told. Every layer had an alibi: no logfire alert covers relay
coverage, /_health returned 200 the whole time, and the dedicated relay-check
schedule had been folded into the cognitive cycle — so looking at the fleet
depended on phi feeling like it (her checks that month tracked a conversation
about correlated failures, and stopped when the conversation did).

This module makes a coverage regression an *event*, riding the same incident
machinery as a logfire alert: a watched host going behind the network opens
an incident in ``bot_status.alert_incidents`` (namespaced ``relay-eval:``),
renders in [ALERT WATCH] under the same escalation doctrine, quiet-closes on
recovery, and wakes phi with the event's content in hand.

The verdict consumed is ``/api/status``'s network-absolute behind-lately,
not ``/api/relays``' self-relative status. During the wedge the self-relative
classifier re-labeled 18% coverage "nominal" within 12 hours, because the
outage had become its own baseline. Behind-vs-the-fleet has no baseline to
poison: a wedged relay stays behind for as long as it wedges.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from bot.config import settings

logger = logging.getLogger("bot.relay_watch")

RELAY_NAMESPACE = "relay-eval:"


def is_relay_key(key: str) -> bool:
    """Whether an incident key belongs to this watcher's namespace."""
    return key.startswith(RELAY_NAMESPACE)


def _status_url() -> str:
    return settings.relays_url.removesuffix("/relays") + "/status"


def relay_states(verdict: dict[str, Any]) -> list[dict[str, Any]]:
    """Alert-shaped states for every watched host in an /api/status verdict.

    Pure. One state per watched host, firing or not — the quiet hosts are
    what lets ``gate_firings`` quiet-close a recovered incident. ``last_run``
    is the eval run's timestamp, so the incident count advances once per
    eval run observed behind, not once per poll.
    """
    watched = set(settings.relay_watch_hosts)
    states: list[dict[str, Any]] = []
    for relay in verdict.get("relays") or []:
        host = relay.get("host", "")
        if host not in watched:
            continue
        latest = relay.get("latest") or {}
        disconnected = not latest.get("connected", True)
        firing = bool(relay.get("behind_lately")) or disconnected
        detail = ""
        if firing:
            detail = (
                f"behind the network in {relay.get('behind_runs', '?')}/"
                f"{relay.get('runs', '?')} recent eval runs, avg coverage "
                f"{relay.get('avg_coverage_pct', 0):.0f}% (latest "
                f"{latest.get('coverage_pct', 0):.0f}%"
                + (", DISCONNECTED" if disconnected else "")
                + ")"
            )
        states.append(
            {
                "key": f"{RELAY_NAMESPACE}{host}",
                "project": "relay-eval",
                "name": host,
                "active": True,
                "snoozed": False,
                "has_matches": firing,
                "last_run": str(latest.get("ts") or ""),
                "detail": detail,
            }
        )
    return states


async def fetch_relay_states() -> list[dict[str, Any]] | None:
    """Watched-host states from relay-eval, or None when unavailable.

    None (rather than []) so a relay-eval outage keeps the last-known
    incident record instead of quiet-closing everything — the same
    hold-on-failure contract the logfire alert poll has.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(_status_url())
            resp.raise_for_status()
            verdict = resp.json()
    except Exception as exc:
        logger.warning(f"relay status fetch failed: {exc}")
        return None
    if not isinstance(verdict, dict):
        return None
    return relay_states(verdict)


def wake_material(
    incidents: dict[str, dict[str, Any]], opened_keys: list[str]
) -> str:
    """The event content a newly opened relay incident wakes phi with.

    This string seeds her recall the way a notification's text does —
    episodic memory and prior coverage key on the host and the numbers,
    so past behavior of this exact relay surfaces before she judges.
    """
    parts = []
    for key in opened_keys:
        inc = incidents.get(key) or {}
        name = inc.get("name", key.removeprefix(RELAY_NAMESPACE))
        detail = inc.get("detail", "")
        parts.append(f"{name}: {detail}" if detail else name)
    return "relay coverage regression — " + "; ".join(parts)
