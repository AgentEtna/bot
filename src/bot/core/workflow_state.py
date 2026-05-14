"""[WORKFLOW STATE] — current state of the operator's workflow automation.

Deterministic per-deployment health, computed in Python from raw flow run
data. No LLM in the classification path.

History: an earlier version handed the run history to a haiku synth agent
and asked it to label each deployment healthy/broken/stuck/degraded. That
synth hallucinated — given a clear run-history with three successes after
a failure cluster, it still labeled the deployment "broken" and cited the
end-time of a successful run as "no successful run since X". Phi did the
right thing by trusting the block; the block was wrong. Now the
classification is pure Python (most-recent terminal-state per deployment)
so it cannot lie. Phi can still call the prefect_* tools for detail.

Naming is deliberately abstract — workflow tooling is prefect today;
tomorrow it could be anything else with the same surface.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from bot.config import settings
from bot.utils.time import relative_when

logger = logging.getLogger("bot.workflow_state")

_TTL_SECONDS = 300  # 5min
_cache: dict[str, Any] = {"text": "", "fetched_at": 0.0}

# Per-deployment "recent fail rate" gate for the degraded label. Looking at
# at most the last 5 terminal runs; if at least 2 of them failed AND the
# most recent run still completed, the deployment is "degraded" (flapping)
# rather than "healthy". Conservative on purpose — too sensitive and
# normal transient failures get surfaced as ongoing.
_DEGRADED_WINDOW = 5
_DEGRADED_FAIL_THRESHOLD = 2

_TERMINAL_STATES = {"COMPLETED", "FAILED", "CRASHED", "CANCELLED"}


def _basic_auth() -> tuple[str, str] | None:
    """Parse PREFECT_API_AUTH_STRING into (user, pass) for httpx basic auth."""
    raw = settings.prefect_api_auth_string
    if not raw or ":" not in raw:
        return None
    user, _, pwd = raw.partition(":")
    return user, pwd


async def _fetch_raw() -> dict[str, Any] | None:
    """Pull recent flow runs + deployments from the prefect REST API."""
    auth = _basic_auth()
    if not auth:
        return None
    base = settings.prefect_api_url.rstrip("/")

    # Stuck candidates: PENDING/RUNNING runs whose expected start was
    # more than an hour ago. They may have been started days ago and
    # would fall out of the recent-activity window. The 1h floor avoids
    # flagging legitimately-running short jobs.
    stuck_cutoff = (
        (datetime.now(UTC) - timedelta(hours=1))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    async with httpx.AsyncClient(timeout=15, auth=auth) as client:
        try:
            # Past-activity states only — SCHEDULED rows are future-pending
            # placeholders that drown out the actual signal.
            runs_resp = await client.post(
                f"{base}/flow_runs/filter",
                json={
                    "limit": 200,
                    "sort": "START_TIME_DESC",
                    "flow_runs": {
                        "state": {
                            "type": {
                                "any_": [
                                    "COMPLETED",
                                    "FAILED",
                                    "CRASHED",
                                    "RUNNING",
                                    "CANCELLED",
                                ]
                            }
                        }
                    },
                },
            )
            runs_resp.raise_for_status()
            runs = runs_resp.json()
        except Exception as e:
            logger.debug(f"workflow_state: failed to fetch runs: {e}")
            return None

        try:
            stuck_resp = await client.post(
                f"{base}/flow_runs/filter",
                json={
                    "limit": 20,
                    "sort": "START_TIME_ASC",
                    "flow_runs": {
                        "state": {"type": {"any_": ["PENDING", "RUNNING"]}},
                        "expected_start_time": {"before_": stuck_cutoff},
                    },
                },
            )
            stuck_resp.raise_for_status()
            stuck = stuck_resp.json()
        except Exception as e:
            logger.debug(f"workflow_state: failed to fetch stuck candidates: {e}")
            stuck = []

        try:
            deps_resp = await client.post(
                f"{base}/deployments/filter",
                json={"limit": 100},
            )
            deps_resp.raise_for_status()
            deployments = deps_resp.json()
        except Exception as e:
            logger.debug(f"workflow_state: failed to fetch deployments: {e}")
            deployments = []

    return {"runs": runs, "stuck": stuck, "deployments": deployments}


def _state_type(run: dict) -> str:
    """Normalize the state-type out of a flow run row.

    The prefect REST API returns it as either a top-level `state_type`
    or nested under `state.type` depending on the endpoint. Take whichever
    is present.
    """
    return run.get("state_type") or (run.get("state") or {}).get("type", "") or ""


def _classify(runs: list[dict], stuck_ids: set[str]) -> tuple[str, str, str]:
    """Classify a single deployment from its runs (most-recent first).

    Returns ``(status, latest, qualifier)`` where:
      - ``status`` is one of healthy/broken/stuck/degraded (the decision label)
      - ``latest`` is the most-recent run state + age (the load-bearing fact —
        always shown first so phi can't miss it). e.g. ``"COMPLETED 42m ago"``
      - ``qualifier`` is the secondary clause that justifies the status
        (e.g. ``"4/5 recent terminals failed"``), or ``""`` if the status is
        self-evident from ``latest``.
    """
    if not runs:
        return "", "", ""

    # If a stuck PENDING/RUNNING row exists for this deployment, that
    # outranks the terminal-state classification — the work isn't getting
    # picked up, regardless of past completions.
    for r in runs:
        if r["id"] in stuck_ids:
            start = r.get("start_time") or r.get("expected_start_time", "")
            when = relative_when(start) if start else ""
            state = _state_type(r).upper() or "PENDING"
            latest = f"{state} since {when}" if when else state
            return "stuck", latest, "work not picked up"

    # Most recent terminal-state run = ground truth for healthy vs broken.
    terminal = [r for r in runs if _state_type(r) in _TERMINAL_STATES]
    if not terminal:
        # Only running / pending runs in the window. Treat as healthy until
        # we have terminal evidence otherwise.
        return "healthy", "no terminal runs in window", ""

    most_recent = terminal[0]
    most_recent_state = _state_type(most_recent).upper()
    most_recent_when = relative_when(most_recent.get("end_time", "")) or relative_when(
        most_recent.get("start_time", "")
    )
    latest = f"{most_recent_state} {most_recent_when}".strip()

    if most_recent_state == "COMPLETED":
        # Healthy unless the recent fail rate is high enough to flag
        # flapping. Look at the last N terminal runs.
        window = terminal[:_DEGRADED_WINDOW]
        fails = sum(1 for r in window if _state_type(r) in {"FAILED", "CRASHED"})
        if fails >= _DEGRADED_FAIL_THRESHOLD:
            return "degraded", latest, f"{fails}/{len(window)} recent terminals failed"
        return "healthy", latest, ""

    if most_recent_state in {"FAILED", "CRASHED"}:
        msg = (most_recent.get("state_message") or "").strip().splitlines()
        first_line = msg[0] if msg else ""
        first_line = first_line[:140] + "…" if len(first_line) > 140 else first_line
        return "broken", latest, first_line

    if most_recent_state == "CANCELLED":
        return "healthy", latest, ""

    return "healthy", latest, ""


def _compose(raw: dict) -> str:
    """Compose the [WORKFLOW STATE] block from raw run / deployment data."""
    runs = raw.get("runs") or []
    stuck = raw.get("stuck") or []
    deployments = raw.get("deployments") or []
    if not runs and not stuck:
        return ""

    dep_names: dict[str, str] = {
        d["id"]: d.get("name", "?") for d in deployments if d.get("id")
    }
    stuck_ids: set[str] = {r["id"] for r in stuck if r.get("id")}

    # Group runs by deployment_id (preserving most-recent-first order).
    by_dep: dict[str, list[dict]] = {}
    for r in runs:
        dep_id = r.get("deployment_id")
        if not dep_id:
            continue  # ad-hoc / orphan runs — skip the per-deployment table
        by_dep.setdefault(dep_id, []).append(r)

    # Make sure every stuck deployment is represented even if no terminal
    # runs landed in the recent-activity window for it.
    for r in stuck:
        dep_id = r.get("deployment_id")
        if not dep_id:
            continue
        by_dep.setdefault(dep_id, []).append(r)

    if not by_dep:
        return ""

    now_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    # Each entry: (status, name, rendered_line). Sort by (status_priority, name)
    # using the structured status rather than re-parsing the line.
    entries: list[tuple[str, str, str]] = []
    for dep_id, dep_runs in by_dep.items():
        status, latest, qualifier = _classify(dep_runs, stuck_ids)
        if not status:
            continue
        name = dep_names.get(dep_id, dep_id[:8])
        # Format: "- name: LATEST_RUN [status — qualifier]"
        # The most-recent run is the leading fact; the bracketed classification
        # is the decision label phi acts on.
        bracket = f"[{status} — {qualifier}]" if qualifier else f"[{status}]"
        entries.append((status, name, f"- {name}: {latest} {bracket}"))

    if not entries:
        return ""

    priority = {"broken": 0, "stuck": 1, "degraded": 2, "healthy": 3}
    entries.sort(key=lambda e: (priority.get(e[0], 99), e[1]))

    body = "\n".join(line for _, _, line in entries)
    return (
        f"[WORKFLOW STATE — current health of the operator's workflow "
        f"automation, refreshed every {_TTL_SECONDS // 60}min, anchored by "
        f"[NOW]={now_iso}. computed deterministically from flow run "
        f"history; for detail call the prefect_* tools.]\n{body}"
    )


async def get_workflow_state_block() -> str:
    """Compose [WORKFLOW STATE] — per-deployment health, anchored by NOW."""
    now = time.time()
    if _cache["text"] and now - _cache["fetched_at"] < _TTL_SECONDS:
        return _cache["text"]

    raw = await _fetch_raw()
    if not raw:
        return ""

    block = _compose(raw)
    if not block:
        return ""

    _cache["text"] = block
    _cache["fetched_at"] = now
    return block
