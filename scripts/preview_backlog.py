"""Read-only peek at phi's unread notification backlog.

Shows what phi will wake up to when resumed — author, reason, and text of
every unread notification — so the operator knows exactly what the first
batch contains before lifting the pause. Notably useful before a
safe-mode resume: the whole point is that nothing is hidden or drained,
so this is the operator reading ahead, not editing.

Read-only: does NOT call updateSeen, does not mark anything read.

Run from the bot/ directory:

    uv run python scripts/preview_backlog.py
"""

import asyncio

from bot.core.atproto_client import bot_client


async def main() -> None:
    await bot_client.authenticate()
    client = bot_client.client

    unread: list = []
    cursor = None
    while True:
        params: dict = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        res = client.app.bsky.notification.list_notifications(params=params)
        batch = res.notifications or []
        unread.extend(n for n in batch if not n.is_read)
        # stop once a page contains any read notification — the backlog is
        # newest-first, so everything after that point has been seen.
        if not res.cursor or any(n.is_read for n in batch):
            break
        cursor = res.cursor

    if not unread:
        print("backlog is empty — nothing unread.")
        return

    print(f"{len(unread)} unread notification(s):\n")
    for n in unread:
        text = ""
        record = getattr(n, "record", None)
        if record is not None:
            text = (getattr(record, "text", "") or "").replace("\n", " ")
        print(f"[{n.indexed_at[:16]}] {n.reason:<8} @{n.author.handle}")
        if text:
            print(f"    {text[:200]}")
        print(f"    {n.uri}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
