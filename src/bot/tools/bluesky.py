"""Bluesky account tools — posting, own posts, URL checks, labels, infra."""

import asyncio
import ipaddress
import socket
import time
from datetime import date
from typing import Annotated
from urllib.parse import urlparse

import httpx
from pydantic import Field
from pydantic_ai import RunContext

from bot.config import settings
from bot.core.atproto_client import bot_client
from bot.core.mentionable import add_handle, get_mentionable_handles, remove_handle
from bot.tools._helpers import PhiDeps, _check_services_impl, _is_owner, _relative_age

# cached relay names, refreshed from the snapshot endpoint. surfaced to
# the LLM via a dynamic system prompt so it picks from real values when
# calling check_relays(name=...).
_RELAY_NAMES_TTL = 300  # 5 minutes
_relay_names_cache: dict = {"names": [], "fetched_at": 0.0}


async def fetch_relay_names() -> list[str]:
    now = time.time()
    if (
        now - _relay_names_cache["fetched_at"] < _RELAY_NAMES_TTL
        and _relay_names_cache["names"]
    ):
        return _relay_names_cache["names"]
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(settings.relays_url)
            r.raise_for_status()
            names = sorted({m.get("name", "") for m in r.json() if m.get("name")})
            _relay_names_cache["names"] = names
            _relay_names_cache["fetched_at"] = now
            return names
    except Exception:
        return _relay_names_cache["names"]  # fall back to last known


def register(agent):
    @agent.tool
    async def get_own_posts(ctx: RunContext[PhiDeps], limit: int = 10) -> str:
        """Read your own recent top-level posts (no replies). Use this instead of list_records when you need to review what you've posted."""
        try:
            posts = await bot_client.get_own_posts(limit=limit)
            if not posts:
                return "no posts found"
            today = date.today()
            lines = []
            for item in posts:
                p = item.post
                text = p.record.text if hasattr(p.record, "text") else ""
                age = (
                    _relative_age(p.indexed_at, today)
                    if hasattr(p, "indexed_at") and p.indexed_at
                    else ""
                )
                age_str = f" ({age})" if age else ""
                lines.append(f"[{p.uri}]{age_str}: {text[:200]}")
            return "\n\n".join(lines)
        except Exception as e:
            return f"failed to get own posts: {e}"

    @agent.tool
    async def check_urls(ctx: RunContext[PhiDeps], urls: list[str]) -> str:
        """Check whether URLs are reachable. Use this before sharing links to verify they actually work. Accepts full URLs (https://...) or bare domains (example.com/path)."""

        async def _check(client: httpx.AsyncClient, url: str) -> str:
            if not url.startswith(("http://", "https://")):
                url = f"https://{url}"
            try:
                hostname = urlparse(url).hostname
                if not hostname:
                    return f"{url} → blocked: no hostname"
                # resolve and check for private/loopback IPs (SSRF protection)
                try:
                    addrs = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: socket.getaddrinfo(hostname, None)
                    )
                except socket.gaierror:
                    return f"{url} → blocked: DNS resolution failed"
                for addr_info in addrs:
                    ip = ipaddress.ip_address(addr_info[4][0])
                    if ip.is_private or ip.is_loopback or ip.is_link_local:
                        return f"{url} → blocked: private IP"

                r = await client.head(url, follow_redirects=True)
                return f"{url} → {r.status_code}"
            except httpx.TimeoutException:
                return f"{url} → timeout"
            except Exception as e:
                return f"{url} → error: {type(e).__name__}"

        async with httpx.AsyncClient(timeout=10) as client:
            results = await asyncio.gather(*[_check(client, u) for u in urls])
        return "\n".join(results)

    @agent.tool
    async def manage_labels(
        ctx: RunContext[PhiDeps], action: str, label: str = ""
    ) -> str:
        """Manage self-labels on your profile. Actions: 'list' to see current labels, 'add' to add a label, 'remove' to remove a label. The 'bot' label marks you as an automated account."""
        from bot.core.profile_manager import (
            add_self_label,
            get_self_labels,
            remove_self_label,
        )

        if action == "list":
            labels = get_self_labels(bot_client.client)
            return f"current self-labels: {labels}" if labels else "no self-labels set"
        elif action == "add":
            if not label:
                return "provide a label value to add"
            labels = add_self_label(bot_client.client, label)
            return f"added '{label}', labels now: {labels}"
        elif action == "remove":
            if not label:
                return "provide a label value to remove"
            labels = remove_self_label(bot_client.client, label)
            return f"removed '{label}', labels now: {labels}"
        else:
            return f"unknown action '{action}', use 'list', 'add', or 'remove'"

    @agent.tool
    async def manage_mentionable(
        ctx: RunContext[PhiDeps], action: str, handle: str = ""
    ) -> str:
        """Manage the list of people who have opted in to being @mentioned by you.
        OWNER-ONLY (restricted to @{settings.owner_handle}).

        Actions: 'list' to see who's opted in, 'add' to add a handle, 'remove' to remove one.

        When someone tells you "you can tag me" or similar, ask the operator
        to confirm before adding them. Never add someone without operator approval."""
        if not _is_owner(ctx):
            return f"only @{settings.owner_handle} can manage the mentionable list"
        if action == "list":
            handles = await get_mentionable_handles()
            if handles:
                return f"opted-in handles: {', '.join(sorted(handles))}"
            return "no one has opted in yet"
        elif action == "add":
            if not handle:
                return "provide a handle to add"
            handles = await add_handle(handle)
            return (
                f"added @{handle} — opted-in list is now: {', '.join(sorted(handles))}"
            )
        elif action == "remove":
            if not handle:
                return "provide a handle to remove"
            handles = await remove_handle(handle)
            return f"removed @{handle} — opted-in list is now: {', '.join(sorted(handles)) or '(empty)'}"
        else:
            return f"unknown action '{action}', use 'list', 'add', or 'remove'"

    @agent.tool
    async def check_services(ctx: RunContext[PhiDeps]) -> str:
        """Check health of the operator's infrastructure (plyr, PDS, prefect, etc) — NOT your own status.
        Do NOT call this when someone asks if you're online — that's about you, not infrastructure.
        Only use during daily reflection or when someone explicitly asks about services/infrastructure."""
        return await _check_services_impl()

    @agent.tool
    async def check_relays(
        ctx: RunContext[PhiDeps],
        name: Annotated[
            str | None,
            Field(
                description=(
                    "Relay hostname (e.g. 'zlay.waow.tech'). In history "
                    "mode, required. In transitions mode, optional filter. "
                    "Valid hostnames are in [KNOWN RELAYS]."
                )
            ),
        ] = None,
        since: Annotated[
            str | None,
            Field(
                description=(
                    "Start of window, ISO 8601 UTC (e.g. '2026-04-16T00:00:00Z'). "
                    "Use with history or transitions to bound the time range."
                )
            ),
        ] = None,
        until: Annotated[
            str | None,
            Field(description="End of window, ISO 8601 UTC. Pairs with since."),
        ] = None,
        transitions: Annotated[
            bool,
            Field(
                description=(
                    "If True, return status-change events instead of coverage "
                    "points. Best for 'when did X happen' questions."
                )
            ),
        ] = False,
        limit: Annotated[
            int | None,
            Field(
                description=(
                    "Recent-N fallback for history mode when since/until "
                    "aren't set. Default ~288 = one day at 5-min cadence."
                )
            ),
        ] = None,
    ) -> str:
        """Check the atproto relay fleet the operator evaluates via relay-eval.

        Three modes:
        - snapshot (default, no args): current status of every relay.
        - history (name=<host>): coverage timeseries for one relay. Bound
          with since/until for a precise window, or use limit for recent-N.
        - transitions (transitions=True): status-change events across the
          fleet. Answers "when did X happen." Optionally filter by name.

        Report headlines verbatim — the service owns interpretation.
        For app health (plyr, PDS, prefect, etc), use check_services."""
        base = settings.relays_url

        if transitions:
            params: dict[str, str | int] = {}
            if name:
                params["name"] = name
            if since:
                params["since"] = since
            if until:
                params["until"] = until
            try:
                async with httpx.AsyncClient(timeout=15) as http:
                    r = await http.get(f"{base}/events", params=params)
                    r.raise_for_status()
                    events = r.json()
            except Exception as e:
                return f"events endpoint unreachable: {e}"

            if not events:
                window = f"{since} → {until}" if since or until else "last 24h"
                scope = f" for {name}" if name else ""
                return f"no transitions{scope} in {window}"

            scope = f" for {name}" if name else " (fleet)"
            lines = [f"transitions{scope}: {len(events)}"]
            for e in events:
                ts = e.get("ts", "")[:16].replace("T", " ")
                n = e.get("name", "?")
                from_s = e.get("from_status", "?")
                to_s = e.get("to_status", "?")
                headline = e.get("headline", "")
                lines.append(f"  {ts}  {n}  {from_s} → {to_s}")
                if headline:
                    lines.append(f'    "{headline}"')
            return "\n".join(lines)

        if name:
            params = {"name": name}
            if since:
                params["since"] = since
            if until:
                params["until"] = until
            if limit:
                params["limit"] = limit
            try:
                async with httpx.AsyncClient(timeout=15) as http:
                    r = await http.get(f"{base}/history", params=params)
                    r.raise_for_status()
                    data = r.json()
            except Exception as e:
                return f"history endpoint unreachable: {e}"

            points = data.get("points", [])
            summary = data.get("summary", {})
            if not points:
                return f"no history found for '{name}'"

            mean = summary.get("mean_coverage_pct", 0)
            lo = summary.get("min_coverage_pct", 0)
            hi = summary.get("max_coverage_pct", 0)
            connected = summary.get("connected_runs", 0)
            total = summary.get("total_runs", 0)

            first_ts = (points[0].get("ts", "") or "")[:16].replace("T", " ")
            last_ts = (points[-1].get("ts", "") or "")[:16].replace("T", " ")

            # downsample if the series is large; phi can narrow the window
            # with since/until for finer detail.
            max_display = 200
            if len(points) <= max_display:
                display = points
                downsample_note = ""
            else:
                step = max(1, len(points) // max_display)
                display = points[::step]
                downsample_note = f"  (downsampled: 1 in {step})"

            lines = [
                f"history for {name}",
                f"  window: {first_ts} → {last_ts} ({total} points)",
                f"  mean {mean:.2f}% | min {lo:.2f}% | max {hi:.2f}% | "
                f"connected {connected}/{total}",
                "",
                f"  series ({len(display)} shown){downsample_note}:",
            ]
            for p in display:
                ts = (p.get("ts", "") or "")[:16].replace("T", " ")
                pct = p.get("coverage_pct", 0)
                conn = "ok" if p.get("connected") else "DISCONNECTED"
                lines.append(f"    {ts}  {pct:5.2f}%  {conn}")
            return "\n".join(lines)

        # snapshot mode
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                r = await http.get(base)
                r.raise_for_status()
                monitors = r.json()
        except Exception as e:
            return f"relay endpoint unreachable: {e}"

        if not isinstance(monitors, list) or not monitors:
            return "no monitors reported"

        by_status: dict[str, list[dict]] = {
            "critical": [],
            "degraded": [],
            "nominal": [],
        }
        for m in monitors:
            status = m.get("status", "unknown")
            by_status.setdefault(status, []).append(m)

        today = date.today()
        lines = []
        for status in ("critical", "degraded", "nominal"):
            items = by_status.get(status, [])
            if not items:
                continue
            lines.append(f"[{status}] ({len(items)})")
            for m in items:
                headline = m.get("headline", m.get("name", "?"))
                last_changed = m.get("last_changed", "")
                age = _relative_age(last_changed, today) if last_changed else ""
                age_str = f" (changed {age})" if age else ""
                lines.append(f"  - {headline}{age_str}")
            lines.append("")

        return "\n".join(lines).rstrip()

    @agent.tool
    async def changelog(ctx: RunContext[PhiDeps], count: int = 10) -> str:
        """See your own recent changes — what was deployed and when.

        Reads commit history from the github mirror (github.com/zzstoatzz/bot).
        Origin is tangled.sh/zzstoatzz.io/bot. Use when you want to know what
        changed, when a feature was added, or why something works differently.
        """
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get(
                    "https://api.github.com/repos/zzstoatzz/bot/commits",
                    params={"per_page": min(count, 30)},
                )
                r.raise_for_status()
                commits = r.json()
            lines = []
            for c in commits:
                date = c["commit"]["author"]["date"][:10]
                msg = c["commit"]["message"].split("\n")[0]
                lines.append(f"[{date}] {msg}")
            return "\n".join(lines)
        except Exception as e:
            return f"failed to fetch changelog: {e}"
