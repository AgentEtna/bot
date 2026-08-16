"""The API rate limit is per client, and actually enforced.

2026-08-16: a scanner sent 593 requests across 497 paths in 40 seconds and
drew zero 429s. `default_limits` was declared on the Limiter but
SlowAPIMiddleware was never added, so only the two decorated routes were
enforced. Keying was also `get_remote_address` — behind fly's proxy that is
the proxy, one shared bucket for every caller (plyr.fm 8b9c0c05).
"""

from unittest.mock import Mock

from bot.utils.rate_limit import client_ip


def _req(headers: dict, client_host: str | None = "172.16.2.106"):
    r = Mock()
    r.headers = headers
    r.client = Mock(host=client_host) if client_host else None
    return r


def test_prefers_fly_client_ip():
    assert client_ip(_req({"fly-client-ip": "203.0.113.7"})) == "203.0.113.7"


def test_falls_back_to_leftmost_forwarded_for():
    req = _req({"x-forwarded-for": "203.0.113.9, 172.16.0.1, 10.0.0.1"})
    assert client_ip(req) == "203.0.113.9"


def test_falls_back_to_peer_when_no_headers():
    assert client_ip(_req({})) == "172.16.2.106"


def test_never_returns_empty():
    assert client_ip(_req({}, client_host=None)) == "unknown"


def test_two_callers_behind_the_proxy_get_different_keys():
    """The bug this fixes: both requests used to key to the proxy address."""
    a = client_ip(_req({"fly-client-ip": "203.0.113.1"}))
    b = client_ip(_req({"fly-client-ip": "203.0.113.2"}))
    assert a != b


def test_limit_is_enforced_on_api_routes_but_not_health():
    """SlowAPIMiddleware is registered, and /health is exempt so fly's
    liveness probe can never be throttled into a machine restart."""
    from starlette.testclient import TestClient

    import bot.main as m

    assert any("SlowAPIMiddleware" in str(mw.cls) for mw in m.app.user_middleware)
    assert m.limiter._key_func is client_ip

    client = TestClient(m.app)
    codes = {client.get("/health").status_code for _ in range(80)}
    assert codes == {200}, f"/health must stay exempt, saw {codes}"


def test_limit_actually_fires_and_is_per_client():
    """The regression: 593 requests in 40s drew zero 429s. A single caller
    must hit the ceiling, and a different caller must not inherit it."""
    from starlette.testclient import TestClient

    import bot.main as m

    client = TestClient(m.app)
    noisy = {"fly-client-ip": "203.0.113.100"}
    codes = [client.get("/api/abilities", headers=noisy).status_code for _ in range(70)]
    assert 429 in codes, f"limit never fired: {sorted(set(codes))}"
    assert codes[0] != 429

    quiet = {"fly-client-ip": "203.0.113.200"}
    assert client.get("/api/abilities", headers=quiet).status_code != 429, (
        "a second caller inherited the first's budget — keying is still shared"
    )
