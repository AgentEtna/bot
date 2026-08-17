"""phi watches the operator's logfire alerts so the operator doesn't have to.

The raw alert channels on Discord are muted; phi polls logfire's alert API
across all the operator's projects and carries the churn as incidents. The
contract with the operator: anything genuinely needing their hands reaches
them as one @-mention per incident; everything else — flapping, self-resolved,
known-cause — is absorbed silently. Tuning observations accumulate in phi's
reflective surfaces, never as tags.

Incident math mirrors core/workflow_failures.py (same window constants,
imported) so the two monitors stay in doctrine lockstep: the unit of news is
the incident, not the recurrence.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import httpx

from bot.config import settings
from bot.core.workflow_failures import ESCALATION_SECONDS, QUIET_CLOSE_SECONDS
from bot.utils.time import humanize_duration

logger = logging.getLogger("bot.alert_watch")

API_BASE = "https://api-us.pydantic.dev/api"

CLOSED_RETENTION_SECONDS = 24 * 3600
"""A quieted incident stays visible for a day as recent history, then drops."""

RENDER_LIMIT = 12


async def fetch_alert_states() -> list[dict[str, Any]] | None:
    """Snapshot every alert's live state, or ``None`` when unavailable.

    One state dict per alert across all readable projects (optionally
    narrowed by ``settings.alert_projects``). Requires an org API key with
    the "Alerts management" scope in ``LOGFIRE_ALERTS_TOKEN``.
    """
    token = settings.logfire.alerts_token
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    wanted = set(settings.alert_projects)
    try:
        async with httpx.AsyncClient(
            base_url=API_BASE, headers=headers, timeout=15
        ) as client:
            resp = await client.get("/v1/projects/")
            resp.raise_for_status()
            projects = [
                p
                for p in resp.json()
                if not wanted or p.get("project_name") in wanted
            ]
            states: list[dict[str, Any]] = []
            for project in projects:
                name = project.get("project_name", "")
                resp = await client.get(f"/v1/projects/{project['id']}/alerts/")
                resp.raise_for_status()
                for alert in resp.json():
                    states.append(_alert_state(name, alert))
            return states
    except Exception as exc:
        logger.warning(f"alert state fetch failed: {exc}")
        return None


def _alert_state(project: str, alert: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": f"{project}:{alert.get('id', '')}",
        "project": project,
        "name": alert.get("name", ""),
        "active": bool(alert.get("active")),
        "snoozed": bool(alert.get("snoozed_until")),
        "has_matches": bool(alert.get("has_matches")),
        "last_run": alert.get("last_run"),
        "detail": _match_detail(alert),
    }


def _match_detail(alert: dict[str, Any]) -> str:
    """First matched row, flattened — the 'what' of a firing alert."""
    rows = (alert.get("result") or {}).get("data") or []
    if not rows:
        return ""
    cols = [c.get("name", "") for c in (alert.get("result") or {}).get("columns", [])]
    pairs = [f"{c}={v}" for c, v in zip(cols, rows[0])]
    return " ".join(" ".join(pairs).split())[:240]


def parse_webhook(payload: Any) -> dict[str, Any] | None:
    """State from a logfire raw-data webhook push. None when unreadable.

    Captured shape (2026-08-17): {organization_name, project_name,
    alert_name, timestamp, n_rows, data, columns, errors, description,
    links}. The alert UUID only appears inside ``links.alert``
    (…/alerts/{uuid}?…) — extracting it keeps push and poll folding into
    the same incident key. The endpoint logs every payload verbatim, so a
    shape change shows up in traces rather than as silent drops.
    """
    if not isinstance(payload, dict):
        return None
    name = payload.get("alert_name")
    project = payload.get("project_name")
    if not name or not project:
        return None
    alert_url = (payload.get("links") or {}).get("alert") or ""
    _, _, tail = alert_url.partition("/alerts/")
    alert_id = tail.split("?", 1)[0].strip("/") or name
    cols = [c.get("name", "") for c in payload.get("columns") or []]
    rows = payload.get("data") or []
    detail = ""
    if rows:
        pairs = [f"{c}={v}" for c, v in zip(cols, rows[0])]
        detail = " ".join(" ".join(pairs).split())[:240] or " ".join(
            str(rows[:3]).split()
        )[:240]
    return {
        "key": f"{project}:{alert_id}",
        "project": project,
        "name": name,
        "active": True,
        "snoozed": False,
        "has_matches": True,
        "last_run": str(payload.get("timestamp") or ""),
        "detail": detail,
    }


def fold_firing(
    state: dict[str, Any],
    incidents: dict[str, dict[str, Any]],
    cursor: dict[str, str],
    now_ts: float,
) -> tuple[bool, dict[str, dict[str, Any]], dict[str, str]]:
    """Fold one pushed firing into the record. Pure.

    Returns (opened, incidents, cursor) — ``opened`` is True when this
    firing started a new incident (the only case that wakes phi). Unlike
    ``gate_firings`` this never closes or prunes anything: a push says one
    alert fired, not that the others are quiet.
    """
    out = {k: dict(v) for k, v in incidents.items()}
    new_cursor = dict(cursor)
    opened = _apply_firing(state, out, new_cursor, now_ts, always_observe=True)
    return opened, out, new_cursor


def _apply_firing(
    state: dict[str, Any],
    incidents: dict[str, dict[str, Any]],
    cursor: dict[str, str],
    now_ts: float,
    always_observe: bool = False,
) -> bool:
    """Mutate ``incidents``/``cursor`` with one firing; True if it opened.

    ``always_observe`` is the push path: each webhook delivery is a real
    notify event, whereas a poll re-reading an unchanged ``last_run`` is
    the same firing seen twice.
    """
    key = state["key"]
    last_run = state.get("last_run") or ""
    observed = always_observe or cursor.get(key) != last_run
    cursor[key] = last_run
    inc = incidents.get(key)
    if inc is None or inc.get("closed_ts"):
        incidents[key] = {
            "opened_ts": now_ts,
            "last_seen_ts": now_ts,
            "count": 1,
            "name": state["name"],
            "project": state["project"],
            "detail": state["detail"],
        }
        return True
    inc["last_seen_ts"] = now_ts
    if observed:
        inc["count"] = inc.get("count", 0) + 1
    if state["detail"]:
        inc["detail"] = state["detail"]
    return False


def gate_firings(
    states: list[dict[str, Any]],
    incidents: dict[str, dict[str, Any]],
    cursor: dict[str, str],
    now_ts: float,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Fold an alert-state snapshot into the incident record. Pure.

    ``incidents`` maps alert key -> {opened_ts, last_seen_ts, count, name,
    project, detail, closed_ts?}. A firing alert opens an incident or feeds
    the open one; ``count`` advances only when the alert's ``last_run``
    moved past ``cursor`` (a re-observed firing is not news). An incident
    quiet-closes after ``QUIET_CLOSE_SECONDS`` without matches and is
    retained as history for ``CLOSED_RETENTION_SECONDS``.
    """
    out: dict[str, dict[str, Any]] = {
        k: dict(v)
        for k, v in incidents.items()
        if not v.get("closed_ts")
        or now_ts - v["closed_ts"] < CLOSED_RETENTION_SECONDS
    }
    new_cursor = dict(cursor)
    for state in states:
        firing = state["active"] and not state["snoozed"] and state["has_matches"]
        if not firing:
            inc = out.get(state["key"])
            if (
                inc
                and not inc.get("closed_ts")
                and now_ts - inc.get("last_seen_ts", now_ts) >= QUIET_CLOSE_SECONDS
            ):
                inc["closed_ts"] = now_ts
            continue
        _apply_firing(state, out, new_cursor, now_ts)
    live_keys = {state["key"] for state in states}
    for key, inc in out.items():
        if (
            key not in live_keys
            and not inc.get("closed_ts")
            and now_ts - inc.get("last_seen_ts", now_ts) >= QUIET_CLOSE_SECONDS
        ):
            inc["closed_ts"] = now_ts
    new_cursor = {k: v for k, v in new_cursor.items() if k in live_keys}
    return out, new_cursor


def _owner_handle() -> str:
    return f"@{settings.owner_handle}"


def render_alert_watch(incidents: dict[str, dict[str, Any]], now_ts: float) -> str:
    """[ALERT WATCH] — the operator's alert stream, read so they don't.

    Perception with a hard doctrine attached: the operator has muted the
    raw channels and trusts phi to hold the line. Default is silence; the
    escalation-eligible flag is computed here (age past the window), so
    prompt drift alone can't make phi chatty.
    """
    open_items = sorted(
        ((k, v) for k, v in incidents.items() if not v.get("closed_ts")),
        key=lambda kv: kv[1].get("opened_ts", 0),
    )
    closed_items = sorted(
        ((k, v) for k, v in incidents.items() if v.get("closed_ts")),
        key=lambda kv: kv[1]["closed_ts"],
        reverse=True,
    )
    if not open_items and not closed_items:
        return ""
    lines = [
        "[ALERT WATCH — the operator's logfire alerts. they have MUTED the "
        "raw channels and trust you to absorb this stream. doctrine: "
        "(1) default is silence — an open incident here is yours to carry, "
        "not a prompt to speak; flapping, self-resolved, and known-cause "
        "firings get absorbed without a word. "
        f"(2) only incidents marked ESCALATION-ELIGIBLE may reach the "
        f"operator, via one {_owner_handle()} mention per incident — and "
        "only when it looks like it needs their hands, not just their "
        "awareness. never mention them twice for the same incident. "
        "(3) tuning observations ('this alert flaps but looks benign') "
        "belong in your daily reflection or retro, never a tag and never "
        "a standalone post.]"
    ]
    for key, inc in open_items[:RENDER_LIMIT]:
        age_s = max(0.0, now_ts - inc.get("opened_ts", now_ts))
        age = humanize_duration(timedelta(seconds=age_s))
        tally = f", {inc['count']} firings" if inc.get("count", 1) > 1 else ""
        detail = f" — {inc['detail']}" if inc.get("detail") else ""
        eligible = (
            " [ESCALATION-ELIGIBLE]" if age_s >= ESCALATION_SECONDS else ""
        )
        lines.append(
            f"- {inc.get('project', '')}/{inc.get('name', key)}: firing, "
            f"opened {age} ago{tally}{eligible}{detail}"
        )
    if len(open_items) > RENDER_LIMIT:
        lines.append(f"- … and {len(open_items) - RENDER_LIMIT} more open")
    for key, inc in closed_items[:RENDER_LIMIT]:
        ago = humanize_duration(
            timedelta(seconds=max(0.0, now_ts - inc["closed_ts"]))
        )
        tally = f" after {inc['count']} firings" if inc.get("count", 1) > 1 else ""
        lines.append(
            f"- {inc.get('project', '')}/{inc.get('name', key)}: quieted "
            f"{ago} ago{tally}"
        )
    return "\n".join(lines)
