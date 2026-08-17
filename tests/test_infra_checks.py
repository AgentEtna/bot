"""Regression tests for check_infra's two silent failure modes.

Both were found on 2026-07-30 only because phi mentioned them in a blog
post — neither reached telemetry, because both paths returned the error to
phi as a string and never logged.

1. The evergreen proxy enforces a host allowlist and 403s a whole batch
   naming the disallowed hosts. `hub.waow.tech` was added to SERVICE_CHECKS
   on 2026-07-24 and to the proxy on 2026-07-30, so for six days every one
   of phi's 14 service checks failed over one unlisted host.
2. The changelog call was unauthenticated: 60 requests/hour per IP.
"""

import logging
from datetime import UTC
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from bot.tools import _helpers
from bot.tools._helpers import _blocked_hosts, _check_services_impl
from bot.tools.bluesky import _is_github_rate_limit


def _response(status: int, json_body, url: str = "https://proxy.test") -> httpx.Response:
    return httpx.Response(
        status_code=status, json=json_body, request=httpx.Request("POST", url)
    )


class TestBlockedHostDegradation:
    BLOCKED = "https://hub.waow.tech/api/agents/discovery-pool"

    def _client_returning(self, *responses):
        client = AsyncMock()
        client.post = AsyncMock(side_effect=list(responses))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx, client

    async def test_one_blocked_host_does_not_lose_every_check(self, caplog):
        """The bug: a single unlisted host 403'd all 14 checks."""
        ok_url = _helpers.SERVICE_CHECKS[0]["url"]
        ctx, client = self._client_returning(
            _response(403, {"error": "blocked hosts", "blocked": [self.BLOCKED]}),
            _response(200, [{"url": ok_url, "status": 200, "ok": True, "ms": 12}]),
        )
        with patch.object(_helpers.httpx, "AsyncClient", return_value=ctx):
            with caplog.at_level(logging.WARNING, logger="bot.tools"):
                out = await _check_services_impl()

        assert client.post.await_count == 2, "should retry without the blocked host"
        retried = client.post.await_args_list[1].kwargs["json"]["checks"]
        assert all(c["url"] != self.BLOCKED for c in retried)

        assert "services healthy" in out
        assert "UNMONITORED" in out, "a dropped host must be reported, not hidden"
        assert "discovery pool (hub)" in out
        # unknown must not read as healthy
        assert "1/1 services healthy" in out

        assert any("allowlist" in r.message for r in caplog.records), (
            "a monitor that stops monitoring must be visible in telemetry"
        )

    async def test_unreachable_proxy_logs(self, caplog):
        ctx, _ = self._client_returning(httpx.ConnectError("boom"))
        with patch.object(_helpers.httpx, "AsyncClient", return_value=ctx):
            with caplog.at_level(logging.WARNING, logger="bot.tools"):
                out = await _check_services_impl()
        assert "unreachable" in out
        assert any("unreachable" in r.message for r in caplog.records)

    @pytest.mark.parametrize(
        "body",
        [
            {"error": "something else"},
            {"error": "blocked hosts"},
            {"error": "blocked hosts", "blocked": "not-a-list"},
            ["not", "a", "dict"],
        ],
    )
    def test_blocked_hosts_parser_is_defensive(self, body):
        assert _blocked_hosts(_response(403, body)) == []


class TestGithubRateLimitDetection:
    def _http_error(self, status: int, headers: dict, text: str = "{}") -> Exception:
        resp = httpx.Response(
            status_code=status,
            headers=headers,
            text=text,
            request=httpx.Request("GET", "https://api.github.com/x"),
        )
        return httpx.HTTPStatusError("boom", request=resp.request, response=resp)

    def test_exhausted_quota_is_recognised(self):
        assert _is_github_rate_limit(
            self._http_error(403, {"x-ratelimit-remaining": "0"})
        )

    def test_secondary_limit_429_is_recognised(self):
        assert _is_github_rate_limit(
            self._http_error(429, {}, text="You have exceeded a secondary rate limit")
        )

    def test_plain_403_is_not_a_rate_limit(self):
        """A 403 with quota remaining is a permissions problem, not throttling."""
        assert not _is_github_rate_limit(
            self._http_error(403, {"x-ratelimit-remaining": "57"}, text="Forbidden")
        )

    def test_non_http_error_is_not_a_rate_limit(self):
        assert not _is_github_rate_limit(httpx.ConnectError("dns"))


class TestRelaySnapshotVerdict:
    """The snapshot merges two views: self-relative statuses from /api/relays
    and the network-absolute behind-lately verdict from /api/status. A relay
    can be 'nominal' against its own baseline while carrying almost nothing —
    without the verdict phi reported such relays as healthy."""

    MONITORS = [
        {"name": "relay.feeds.blue", "status": "nominal", "headline": "relay.feeds.blue: 0% coverage over last 3 eval runs"}
    ]
    VERDICT = {
        "window": {"runs": 24},
        "relays": [
            {
                "host": "relay.feeds.blue",
                "behind_lately": True,
                "behind_runs": 24,
                "runs": 24,
                "avg_coverage_pct": 0.0,
                "latest": {"coverage_pct": 0.1},
            }
        ],
    }

    def _client(self, *responses):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=list(responses))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def test_verdict_appended_to_snapshot(self):
        from bot.tools import bluesky
        from bot.tools.bluesky import _relay_snapshot_impl

        ctx = self._client(
            _response(200, self.MONITORS), _response(200, self.VERDICT)
        )
        with patch.object(bluesky.httpx, "AsyncClient", return_value=ctx):
            out = await _relay_snapshot_impl("https://x.test/api/relays")

        assert "behind the network lately (1 of 1, last 24 runs)" in out
        assert "relay.feeds.blue: behind in 24/24 runs" in out

    async def test_status_failure_degrades_not_fails(self):
        from bot.tools import bluesky
        from bot.tools.bluesky import _relay_snapshot_impl

        ctx = self._client(
            _response(200, self.MONITORS), httpx.ConnectError("boom")
        )
        with patch.object(bluesky.httpx, "AsyncClient", return_value=ctx):
            out = await _relay_snapshot_impl("https://x.test/api/relays")

        assert "[nominal]" in out, "the self-relative snapshot must survive"
        assert "behind-lately verdict unavailable" in out
