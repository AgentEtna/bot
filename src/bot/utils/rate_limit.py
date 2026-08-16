"""Per-client keying for the API rate limiter.

`slowapi.util.get_remote_address` reads `request.client.host`, which behind
fly's proxy is the proxy: every inbound span records the same `172.16.2.106`
regardless of caller. Keyed that way, a global limit is one shared bucket for
the whole site — worse than no limit, because a single scanner spends
everyone's budget and phi's own cockpit starts 429ing. plyr.fm shipped this
exact bug and measured it: 298 429s on one polling endpoint, listeners
knocking each other offline (plyr.fm 8b9c0c05).

fly forwards the caller in `Fly-Client-IP`; `X-Forwarded-For` is the fallback,
whose leftmost entry is the original client and the rest are proxies.
"""

from starlette.requests import Request

_CLIENT_IP_HEADER = "fly-client-ip"
_FORWARDED_FOR_HEADER = "x-forwarded-for"


def client_ip(request: Request) -> str:
    """The caller's address, or a stable placeholder when it can't be found."""
    if fly_ip := request.headers.get(_CLIENT_IP_HEADER):
        return fly_ip.strip()
    if forwarded := request.headers.get(_FORWARDED_FOR_HEADER):
        if first := forwarded.split(",")[0].strip():
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
