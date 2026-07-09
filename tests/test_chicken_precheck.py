"""Regression tests for the pre-lock chicken market check slot.

The slot fires on the UTC clock (rounds lock at 06:00 UTC), at most once
per UTC day, and never while paused. We bypass __init__ so no agent or
network machinery is constructed.
"""

from datetime import UTC, datetime
from unittest.mock import patch

from bot.services.notification_poller import NotificationPoller


def _bare_poller() -> NotificationPoller:
    poller = NotificationPoller.__new__(NotificationPoller)
    poller._last_chicken_precheck_date = None
    return poller


def _at(hour: int, day: int = 9) -> datetime:
    return datetime(2026, 7, day, hour, 30, tzinfo=UTC)


def _should(poller, now, paused=False, precheck_hour=4):
    with (
        patch("bot.services.notification_poller.datetime") as dt,
        patch("bot.services.notification_poller.bot_status") as status,
        patch("bot.services.notification_poller.settings") as settings,
    ):
        dt.now.return_value = now
        status.paused = paused
        settings.chicken_precheck_utc_hour = precheck_hour
        return poller._should_chicken_precheck()


def test_fires_at_configured_utc_hour():
    assert _should(_bare_poller(), _at(hour=4)) is True


def test_silent_at_other_hours():
    poller = _bare_poller()
    for hour in [0, 3, 5, 12, 21]:
        assert _should(poller, _at(hour=hour)) is False


def test_fires_once_per_utc_day():
    poller = _bare_poller()
    poller._last_chicken_precheck_date = _at(hour=4).date()
    assert _should(poller, _at(hour=4)) is False
    # next UTC day it fires again
    assert _should(poller, _at(hour=4, day=10)) is True


def test_silent_while_paused():
    assert _should(_bare_poller(), _at(hour=4), paused=True) is False
