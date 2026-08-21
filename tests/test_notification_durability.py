"""Mark-seen timing: a batch the process dies holding must stay unread.

2026-08-07: a mention landed mid-deploy; the poller marked it seen at
dispatch, the machine restarted before the run replied, and phi never saw
the thread. Seen now happens after the handler finishes — died-holding
batches are re-fetched unread by the next process.
"""

import asyncio
from unittest.mock import AsyncMock, Mock

from bot.services.notification_poller import NotificationPoller


def _poller():
    client = Mock()
    client.mark_notifications_seen = AsyncMock()
    poller = NotificationPoller.__new__(NotificationPoller)
    poller.client = client
    poller._semaphore = asyncio.Semaphore(1)
    poller._batch_task = None
    poller._processed_uris = set()
    poller._background_tasks = set()
    poller.handler = Mock()
    return poller


async def test_seen_marked_only_after_handler_completes():
    poller = _poller()
    order: list[str] = []

    async def handle_batch(batch):
        order.append("handled")

    poller.handler.handle_batch = handle_batch
    poller.client.mark_notifications_seen.side_effect = lambda t: order.append(
        "seen"
    ) or asyncio.sleep(0)
    await poller._handle_batch_with_semaphore(["n"], "t0")
    assert order == ["handled", "seen"]


async def test_crash_mid_batch_leaves_notifications_unread():
    """Process death (task cancellation) before the handler finishes must
    not mark seen — this is the regression: at dispatch-time marking, the
    2026-08-07 mention was consumed by a deploy restart."""
    poller = _poller()
    started = asyncio.Event()

    async def hang(batch):
        started.set()
        await asyncio.sleep(60)

    poller.handler.handle_batch = hang
    task = asyncio.create_task(poller._handle_batch_with_semaphore(["n"], "t0"))
    await started.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    poller.client.mark_notifications_seen.assert_not_awaited()


async def test_handler_failure_still_marks_seen():
    """A poison batch must not be retried every 10s forever."""
    poller = _poller()

    async def boom(batch):
        raise RuntimeError("model down")

    poller.handler.handle_batch = boom
    await poller._handle_batch_with_semaphore(["n"], "t0")
    poller.client.mark_notifications_seen.assert_awaited_once_with("t0")


async def test_follow_ups_wait_for_the_in_flight_run_and_batch_together():
    """2026-08-21: three devlog posts ~25s apart became three concurrent
    one-item runs and seven replies. A second dispatch while one run is in
    flight must be declined and leave its items unclaimed, so the next poll
    after the run finishes batches everything that arrived."""
    poller = _poller()
    release = asyncio.Event()
    handled: list[list] = []

    async def handle_batch(batch):
        handled.append(list(batch))
        await release.wait()

    poller.handler.handle_batch = handle_batch
    first = Mock(uri="at://x/1")
    second = Mock(uri="at://x/2")
    third = Mock(uri="at://x/3")

    assert poller._dispatch_batch([first], "t0") is True
    await asyncio.sleep(0)
    assert poller._dispatch_batch([second], "t1") is False
    assert second.uri not in poller._processed_uris

    release.set()
    await poller._batch_task
    assert poller._dispatch_batch([second, third], "t2") is True
    await poller._batch_task
    assert handled == [[first], [second, third]]
