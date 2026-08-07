"""[PERSONA EXPERIMENT] — the try-on rack. TTL is the gate."""

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from bot.core.atproto_client import BotClient
from bot.core.persona import PERSONA_MAX_CHARS, render_persona, try_on


def _value(days_left=2.0, text="terse oulipian: every post a formal object"):
    now = datetime.now(UTC)
    return {
        "text": text,
        "adoptedAt": (now - timedelta(days=1)).isoformat(),
        "expiresAt": (now + timedelta(days=days_left)).isoformat(),
    }


def test_live_persona_renders_with_expiry_and_guardrails():
    block = render_persona(_value())
    assert "[PERSONA EXPERIMENT" in block
    assert "expires in 1d" in block
    assert "craft rules" in block
    assert "every post a formal object" in block


def test_expired_persona_renders_nothing():
    assert render_persona(_value(days_left=-0.1)) == ""


def test_malformed_or_empty_persona_renders_nothing():
    assert render_persona({}) == ""
    assert render_persona({"text": "x", "expiresAt": "not-a-date"}) == ""
    assert render_persona(_value(text="   ")) == ""


async def test_try_on_rejects_essays_and_bad_ttls():
    with pytest.raises(ValueError, match="not an essay"):
        await try_on(cast(BotClient, None), "x" * (PERSONA_MAX_CHARS + 1), 3)
    with pytest.raises(ValueError, match="days"):
        await try_on(cast(BotClient, None), "a stance", 8)
    with pytest.raises(ValueError, match="empty"):
        await try_on(cast(BotClient, None), "  ", 3)
