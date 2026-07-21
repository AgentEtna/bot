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
        "[NEW WORKFLOW FAILURES — newly observed terminal events; each run ID "
        "is delivered once even if a later run recovered.]"
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
        lines.append(
            f"- {name}: {str(state).upper()} at {ended}; run_id={run_id}{detail}"
        )
    return "\n".join(lines)
