"""Websocket handshakes are refused cleanly, not with an AssertionError.

2026-08-16: a Go crawler's ws upgrade on `/` produced bare AssertionErrors
in the ASGI stack (phi has no inbound websocket routes)."""

import pytest


async def test_ws_scope_closes_before_reaching_app():
    from bot.main import RejectWebsockets

    inner_called = False

    async def inner(scope, receive, send):
        nonlocal inner_called
        inner_called = True

    sent = []

    async def send(msg):
        sent.append(msg)

    mw = RejectWebsockets(inner)
    await mw({"type": "websocket", "path": "/"}, None, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]
    assert not inner_called


async def test_http_scope_passes_through():
    from bot.main import RejectWebsockets

    seen = {}

    async def inner(scope, receive, send):
        seen["scope"] = scope["type"]

    mw = RejectWebsockets(inner)
    await mw({"type": "http", "path": "/"}, None, lambda m: None)
    assert seen["scope"] == "http"
