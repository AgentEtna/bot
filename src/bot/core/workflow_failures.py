"""Event-like detection of new Prefect Failed/Crashed flow runs."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from bot.config import settings

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


def render_failure_block(failures: list[dict[str, Any]]) -> str:
    """Render exact incident facts for Phi's alert pass."""
    lines = [
        "[WORKFLOW INCIDENTS — 'new' = a flow just entered a failing state; "
        "'ongoing' = it has kept failing since the first alert (repeats in "
        "between were counted, not delivered).]"
    ]
    for run in failures[:10]:
        state = run.get("state_type") or (run.get("state") or {}).get("type", "")
        message = " ".join((run.get("state_message") or "").split())
        if len(message) > 240:
            message = message[:239].rstrip() + "…"
        run_id = run.get("id", "")
        name = run.get("name") or run_id[:8]
        ended = run.get("end_time") or "unknown time"
        detail = f" — {message}" if message else ""
        kind = run.get("_incident", "new")
        count = run.get("_count", 1)
        tally = f" ({count} failures this incident)" if count > 1 else ""
        lines.append(
            f"- [{kind}]{tally} {name}: {str(state).upper()} at {ended}; "
            f"run_id={run_id}{detail}"
        )
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
