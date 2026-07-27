"""Bluesky account tools — posting, own posts, URL checks, labels, infra."""

import asyncio
import ipaddress
import socket
import time
from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlparse

import httpx
from pydantic import Field
from pydantic_ai import RunContext

from bot.config import settings
from bot.core.atproto_client import bot_client
from bot.core.mentionable import add_handle, get_mentionable_handles, remove_handle
from bot.tools._helpers import PhiDeps, _check_services_impl, _is_owner, _relative_age

# A commit message carries its reasoning in the body, so the body has to
# survive — but phi's own messages run long, and a hundred of them would
# crowd out everything else in her context.
COMMIT_BODY_LIMIT = 1400
CHANGELOG_CHAR_BUDGET = 24000

# cached relay names, refreshed from the snapshot endpoint. surfaced to
# the LLM via a dynamic system prompt so it picks from real values when
# calling check_infra(aspect="relays", name=...).
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
    async def manage_account(
        ctx: RunContext[PhiDeps],
        setting: Annotated[
            Literal["labels", "mentionable"],
            Field(
                description=(
                    "labels: self-labels on your profile (e.g. 'bot'). "
                    "mentionable: who has opted in to being @mentioned by you "
                    "(owner-only)."
                )
            ),
        ],
        action: Annotated[
            Literal["list", "add", "remove"],
            Field(description="list current values, add one, or remove one"),
        ],
        value: Annotated[
            str,
            Field(description="[add/remove] the label value or handle to add/remove"),
        ] = "",
    ) -> str:
        """Manage your account settings: profile self-labels or the mention opt-in list.

        The mentionable list is OWNER-ONLY — when someone tells you "you can
        tag me", ask the operator to confirm before adding them; never add
        someone without operator approval.
        """
        if setting == "labels":
            from bot.core.profile_manager import (
                add_self_label,
                get_self_labels,
                remove_self_label,
            )

            if action == "list":
                labels = get_self_labels(bot_client.client)
                return (
                    f"current self-labels: {labels}" if labels else "no self-labels set"
                )
            if not value:
                return f"provide a label value to {action}"
            if action == "add":
                labels = add_self_label(bot_client.client, value)
                return f"added '{value}', labels now: {labels}"
            labels = remove_self_label(bot_client.client, value)
            return f"removed '{value}', labels now: {labels}"

        if not _is_owner(ctx):
            return f"only @{settings.owner_handle} can manage the mentionable list"
        if action == "list":
            handles = await get_mentionable_handles()
            if handles:
                return f"opted-in handles: {', '.join(sorted(handles))}"
            return "no one has opted in yet"
        if not value:
            return f"provide a handle to {action}"
        if action == "add":
            handles = await add_handle(value)
            return (
                f"added @{value} — opted-in list is now: {', '.join(sorted(handles))}"
            )
        handles = await remove_handle(value)
        return f"removed @{value} — opted-in list is now: {', '.join(sorted(handles)) or '(empty)'}"

    @agent.tool
    async def check_infra(
        ctx: RunContext[PhiDeps],
        aspect: Annotated[
            Literal["services", "relays", "changelog"],
            Field(
                description=(
                    "services: health of the operator's apps (plyr, PDS, "
                    "prefect, ...). relays: the atproto relay fleet via "
                    "relay-eval (snapshot/history/transitions — see the "
                    "relay params). changelog: your own development "
                    "history — commits with their full messages, which is "
                    "where the reasoning for a change lives, not just what "
                    "changed. windowable with since/until and count."
                )
            ),
        ] = "services",
        count: Annotated[
            int,
            Field(
                description=(
                    "[changelog] how many commits to return (max 100). pair "
                    "with since/until to walk backwards through history a "
                    "window at a time rather than asking for everything."
                )
            ),
        ] = 10,
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
                    "Use with relay history/transitions, or with changelog to "
                    "read an earlier period of your own development."
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
        """Check the operator's infrastructure — NOT your own status.

        Do NOT call this when someone asks if you're online; that's about you.

        aspect='services': health checks for the operator's apps.
        aspect='changelog': your own recent deploys (github mirror of the bot
        repo; origin is tangled.sh/zzstoatzz.io/bot) — what changed and when.
        aspect='relays': the relay fleet via relay-eval, in three modes:
        - snapshot (default, no relay params): current status of every relay.
        - history (name=<host>): coverage timeseries for one relay. Bound
          with since/until for a precise window, or use limit for recent-N.
        - transitions (transitions=True): status-change events across the
          fleet. Answers "when did X happen." Optionally filter by name.
        Report relay headlines verbatim — the service owns interpretation."""
        if aspect == "services":
            return await _check_services_impl()

        if aspect == "changelog":
            # Full commit messages, not just subjects. The subject says what
            # changed; the body says why, and the why is the part that is
            # not reconstructable from the diff. Each message is truncated
            # individually and the whole response is bounded, so a wide
            # window degrades into "narrow it" rather than flooding context.
            params: dict[str, str | int] = {"per_page": max(1, min(count, 100))}
            if since:
                params["since"] = since
            if until:
                params["until"] = until
            try:
                async with httpx.AsyncClient(timeout=15) as http:
                    r = await http.get(
                        "https://api.github.com/repos/zzstoatzz/bot/commits",
                        params=params,
                    )
                    r.raise_for_status()
                    commits = r.json()
            except Exception as e:
                return f"failed to fetch changelog: {e}"

            if not commits:
                return (
                    "no commits in that window. the repo starts 2025-07; "
                    "widen since/until."
                )

            entries: list[str] = []
            total = 0
            for c in commits:
                when = c["commit"]["author"]["date"][:10]
                sha = c["sha"][:8]
                message = c["commit"]["message"].strip()
                if len(message) > COMMIT_BODY_LIMIT:
                    message = message[: COMMIT_BODY_LIMIT - 1].rstrip() + "…"
                entry = f"[{when} {sha}] {message}"
                if total + len(entry) > CHANGELOG_CHAR_BUDGET:
                    entries.append(
                        f"… stopped at {len(entries)} of {len(commits)} commits "
                        "(response budget). narrow the window with since/until."
                    )
                    break
                entries.append(entry)
                total += len(entry)
            return "\n\n".join(entries)

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
