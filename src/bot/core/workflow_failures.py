"""Event-like detection of new Prefect Failed/Crashed flow runs."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import httpx

from bot.config import settings
from bot.utils.time import humanize_duration

logger = logging.getLogger("bot.workflow_failures")


def _basic_auth() -> tuple[str, str] | None:
    raw = settings.prefect_api_auth_string
    if not raw or ":" not in raw:
        return None
    user, _, password = raw.partition(":")
    return user, password


async def fetch_recent_failures() -> list[dict[str, Any]] | None:
    """Fetch recent terminal failures, or ``None`` when monitoring is unavailable."""
    auth = _basic_auth()
    if not auth:
        return None
    base = settings.prefect_api_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15, auth=auth) as client:
            response = await client.post(
                f"{base}/flow_runs/filter",
                json={
                    "limit": 50,
                    "sort": "END_TIME_DESC",
                    "flow_runs": {"state": {"type": {"any_": ["FAILED", "CRASHED"]}}},
                },
            )
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        logger.warning(f"workflow failure fetch failed: {exc}")
        return None


def unseen_failures(
    failures: list[dict[str, Any]], seen_run_ids: list[str]
) -> list[dict[str, Any]]:
    """Return failures whose run IDs have not already been delivered."""
    seen = set(seen_run_ids)
    return [run for run in failures if run.get("id") and run["id"] not in seen]


PENDING_MAX_AGE_SECONDS = 24 * 3600
"""Drop an unaddressed incident after a day. Pressure should build, but an
incident phi never speaks to shouldn't sit in her context forever."""

PENDING_RENDER_LIMIT = 10


def add_pending(
    pending: dict[str, dict[str, Any]],
    failures: list[dict[str, Any]],
    now_ts: float,
) -> dict[str, dict[str, Any]]:
    """Record alert-worthy failures as *unaddressed*, not as delivered.

    Pure. The old flow marked a failure delivered the moment it dispatched a
    run that was ordered to post about it. Here it stays pending until phi
    actually says something, so choosing silence is visible instead of
    indistinguishable from having spoken.
    """
    out = {
        k: dict(v)
        for k, v in pending.items()
        if now_ts - v.get("first_seen_ts", 0) < PENDING_MAX_AGE_SECONDS
    }
    for run in failures:
        run_id = run.get("id")
        if not run_id:
            continue
        existing = out.get(run_id, {})
        out[run_id] = {
            "first_seen_ts": existing.get("first_seen_ts", now_ts),
            "name": run.get("name") or run_id[:8],
            "state": str(
                run.get("state_type") or (run.get("state") or {}).get("type", "")
            ).upper(),
            "message": " ".join((run.get("state_message") or "").split())[:240],
            "incident": run.get("_incident", "new"),
            "count": run.get("_count", 1),
        }
    return out


def _owner_handle() -> str:
    """The operator's handle, prefixed. An alert nobody is tagged in is not
    an alert — 2026-07-25, a memory-synthesis failure went out untagged and
    the operator never saw it."""
    from bot.config import settings

    return f"@{settings.owner_handle}"


def render_pending_block(pending: dict[str, dict[str, Any]], now_ts: float) -> str:
    """[WORKFLOW INCIDENTS] — the operator's infrastructure, as something phi
    has noticed and not yet spoken to.

    This is perception, not an order. It carries the facts and how long each
    has gone unaddressed; what to do about them is phi's call, like any other
    block. Ages are the pressure — an incident she keeps passing over reads
    as older every run.
    """
    if not pending:
        return ""
    items = sorted(pending.items(), key=lambda kv: kv[1].get("first_seen_ts", 0))
    lines = [
        "[WORKFLOW INCIDENTS — the operator's flows that have failed and that "
        "you haven't said anything about yet. they stay here until you do. "
        f"the ids and timestamps are for your reasoning; the operator reads "
        f"bluesky, not a log — and only finds out if you @-mention "
        f"{_owner_handle()}, since a post they aren't tagged in never reaches "
        "them.]"
    ]
    for run_id, inc in items[:PENDING_RENDER_LIMIT]:
        age = humanize_duration(
            timedelta(seconds=max(0.0, now_ts - inc.get("first_seen_ts", now_ts)))
        )
        tally = (
            f", {inc['count']} failures this incident"
            if inc.get("count", 1) > 1
            else ""
        )
        detail = f" — {inc['message']}" if inc.get("message") else ""
        lines.append(
            f"- {inc.get('name', run_id[:8])}: {inc.get('state', '')} "
            f"first seen {age} ago{tally} (run_id={run_id}){detail}"
        )
    if len(items) > PENDING_RENDER_LIMIT:
        lines.append(f"- … and {len(items) - PENDING_RENDER_LIMIT} more")
    return "\n".join(lines)


# --- incident gating -------------------------------------------------------
#
# run-ID dedup alone made phi a pager: a deployment failing hourly produced
# one public alert per run (2026-07-23, ingest). the unit of news is the
# INCIDENT — a flow entering a failing state — not each recurrence inside
# one. while an incident is open, new failures are counted silently; at
# most one "still failing" escalation goes out per window. an incident
# closes after a quiet window so the next failure is news again.

ESCALATION_SECONDS = 6 * 3600
QUIET_CLOSE_SECONDS = 6 * 3600


def incident_key(run: dict[str, Any]) -> str:
    """Stable identity of the failing flow: deployment, else name prefix.

    Prefect run names look like '<flow>-<8 hex>'; stripping the trailing
    hex suffix groups runs of the same flow when deployment_id is absent.
    """
    dep = run.get("deployment_id")
    if dep:
        return f"dep:{dep}"
    name = run.get("name") or ""
    stem, _, tail = name.rpartition("-")
    if stem and len(tail) == 8 and all(c in "0123456789abcdef" for c in tail):
        return f"flow:{stem}"
    return f"run:{name or run.get('id', '')}"


def gate_alerts(
    new_failures: list[dict[str, Any]],
    incidents: dict[str, dict[str, Any]],
    now_ts: float,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Decide which failures are alert-worthy; return (to_alert, incidents).

    Pure: mutates nothing. `incidents` maps incident_key -> {opened_ts,
    alerted_ts, count}. Every new failure updates its incident; a failure
    is alert-worthy when it opens an incident or when the escalation
    window has passed since the last alert for that incident.
    """
    out: dict[str, dict[str, Any]] = {
        k: dict(v)
        for k, v in incidents.items()
        if now_ts - v.get("last_seen_ts", v.get("opened_ts", 0)) < QUIET_CLOSE_SECONDS
    }
    to_alert: list[dict[str, Any]] = []
    for run in new_failures:
        key = incident_key(run)
        inc = out.get(key)
        if inc is None:
            out[key] = {
                "opened_ts": now_ts,
                "alerted_ts": now_ts,
                "last_seen_ts": now_ts,
                "count": 1,
            }
            to_alert.append({**run, "_incident": "new", "_count": 1})
            continue
        inc["count"] = inc.get("count", 0) + 1
        inc["last_seen_ts"] = now_ts
        if now_ts - inc.get("alerted_ts", 0) >= ESCALATION_SECONDS:
            inc["alerted_ts"] = now_ts
            to_alert.append({**run, "_incident": "ongoing", "_count": inc["count"]})
    return to_alert, out
