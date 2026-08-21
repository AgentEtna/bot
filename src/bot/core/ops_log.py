"""Durable log of phi's own repo operations, tailed from jetstream.

[RECENT OPERATIONS] used to be built from ``listRecords`` — a snapshot of
surviving state. A snapshot cannot show a delete (the record is simply
absent) and shows an edit only as its final text, indistinguishable from a
fresh create. Awareness of what happened to the repo needs the repo's
event log, so this module tails jetstream (server-side filtered to phi's
DID — bandwidth is only her own events) and appends every commit op to a
JSONL file on the fly volume.

Two consumers:
- ``read_ops`` — the [RECENT OPERATIONS] block renders from this window.
- post creates are forwarded to the own-posts index (prior-coverage
  recall) via the ``on_post`` callback, so the semantic index of phi's
  own output stays live rather than daily-stale.

Ops arriving here were made by *anyone* holding credentials — this
process, phi's hosted MCP tools, or an external service. Ops performed by
this process register themselves via ``record_local_write`` so the block
can distinguish "you did this here" from "this happened to your repo";
the unattributed remainder is exactly the signal that caught nothing
before (e.g. a backend rewriting cosmik collections with no phi-side
call).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypedDict

from bot.config import settings

logger = logging.getLogger("bot.ops_log")

RETENTION_DAYS = 14
_RECONNECT_MAX_SECONDS = 60
# collections whose record body we keep (for summaries); everything else
# logs op/nsid/rkey only, so likes on huge embeds don't bloat the file.
RECORD_KEPT_NSIDS: frozenset[str] = frozenset(
    {
        "app.bsky.feed.post",
        "io.zzstoatzz.phi.goal",
        "network.cosmik.card",
        "network.cosmik.connection",
        "app.greengale.document",
    }
)

# (nsid, rkey) pairs written by THIS process — see record_local_write.
_local_writes: set[tuple[str, str]] = set()


class OpRow(TypedDict):
    """One repo operation as stored in the JSONL log."""

    time_us: int
    at: str  # iso timestamp derived from time_us
    op: str  # create | update | delete
    nsid: str
    rkey: str
    local: bool  # written by this process (best-effort attribution)
    record: dict[str, Any] | None


def record_local_write(uri: str) -> None:
    """Mark an AT-URI as written by this process, for attribution.

    Best-effort: covers writes made through BotClient (posts, likes,
    reposts, follows). Writes phi makes through hosted MCP tools arrive
    unattributed — the block renders those neutrally rather than claiming
    an actor it can't know.
    """
    parts = uri.split("/")
    if len(parts) >= 2:
        _local_writes.add((parts[-2], parts[-1]))
    else:
        logger.debug(f"could not parse uri for attribution: {uri}")


def _log_path() -> Path:
    return Path(settings.ops_log_path)


def _iso_from_us(time_us: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(time_us / 1_000_000, tz=UTC).isoformat()


def append_op(row: OpRow) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def read_ops(window_hours: float = 48.0) -> list[OpRow]:
    """Ops within the wall-clock window, oldest first. Tolerates a missing
    or partially-corrupt file (a torn tail write loses one row, not the log)."""
    path = _log_path()
    if not path.exists():
        return []
    cutoff_us = int((time.time() - window_hours * 3600) * 1_000_000)
    rows: list[OpRow] = []
    seen: set[tuple] = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("time_us", 0) < cutoff_us:
                continue
            # reconnects rewind the cursor 5s so a crash can't skip ops;
            # the price is replayed appends, deduped here at read time.
            key = (row.get("time_us"), row.get("nsid"), row.get("rkey"), row.get("op"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    rows.sort(key=lambda r: r["time_us"])
    return rows


def last_cursor_us() -> int | None:
    """time_us of the newest logged op, for jetstream resume."""
    path = _log_path()
    if not path.exists():
        return None
    newest: int | None = None
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                t = json.loads(line).get("time_us")
            except json.JSONDecodeError:
                continue
            if t and (newest is None or t > newest):
                newest = t
    return newest


def prune_log() -> None:
    """Rewrite the file keeping RETENTION_DAYS of history."""
    path = _log_path()
    if not path.exists():
        return
    cutoff_us = int((time.time() - RETENTION_DAYS * 86400) * 1_000_000)
    kept: list[str] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                if json.loads(line).get("time_us", 0) >= cutoff_us:
                    kept.append(line)
            except json.JSONDecodeError:
                continue
    path.write_text("".join(kept), encoding="utf-8")


def event_to_row(event: dict[str, Any]) -> OpRow | None:
    """Convert one jetstream message to an OpRow. Non-commit events → None."""
    if event.get("kind") != "commit":
        return None
    commit = event.get("commit") or {}
    op = commit.get("operation", "")
    nsid = commit.get("collection", "")
    rkey = commit.get("rkey", "")
    if not (op and nsid and rkey):
        return None
    time_us = int(event.get("time_us") or 0)
    record = commit.get("record") if nsid in RECORD_KEPT_NSIDS else None
    return OpRow(
        time_us=time_us,
        at=_iso_from_us(time_us),
        op=op,
        nsid=nsid,
        rkey=rkey,
        local=(nsid, rkey) in _local_writes,
        record=record,
    )


PULL_COMMENT_NSID = "sh.tangled.repo.pull.comment"


WATCHED_CURSOR_FILE = Path("/data/watched_cursor.json")


def _watched_cursor() -> int:
    try:
        return int(json.loads(WATCHED_CURSOR_FILE.read_text())["time_us"])
    except Exception:
        return 0


def _set_watched_cursor(time_us: int) -> None:
    try:
        WATCHED_CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
        WATCHED_CURSOR_FILE.write_text(json.dumps({"time_us": time_us}))
    except Exception as e:
        logger.warning(f"failed to persist watched cursor: {e}")


def pull_comment_material(record: dict[str, Any], commenter: str) -> str:
    """The event content a pull-request comment wakes phi with."""
    pull = str(record.get("pull") or "")
    body = str(record.get("body") or "").strip()
    return f"@{commenter} commented on your pull request {pull}:\n\n{body}"


class OpsLogConsumer:
    """Long-lived jetstream tail of phi's own repo, symmetric with the
    notification poller: constructed in the lifespan, start()/stop().

    The same socket can watch other repos for events *about* phi. The first
    such event: a `sh.tangled.repo.pull.comment` in a watched repo (the
    operator's) whose `pull` is one of phi's pull requests. Those are not
    ops on her repo and never enter the ops log; they go to
    ``on_pull_comment`` and wake her, so a review comment reaches her the
    way a mention does — without anyone posting on bluesky about it.
    """

    def __init__(
        self,
        did: str,
        on_post: Callable[[OpRow], Awaitable[None]] | None = None,
        watch_dids: tuple[str, ...] = (),
        on_pull_comment: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ):
        self.did = did
        self.on_post = on_post
        self.watch_dids = tuple(d for d in watch_dids if d and d != did)
        self.on_pull_comment = on_pull_comment
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        prune_log()
        self._task = asyncio.create_task(self._run(), name="ops-log-jetstream")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _url(self) -> str:
        url = f"{settings.jetstream_url}?wantedDids={self.did}"
        for did in self.watch_dids:
            url += f"&wantedDids={did}"
        cursor = last_cursor_us()
        if cursor:
            # rewind 5s so a crash between receive and append can't skip ops;
            # append is keyed by time_us so replays are visible but harmless
            # (read_ops callers dedupe by (time_us, nsid, rkey) implicitly
            # via identical rows).
            url += f"&cursor={cursor - 5_000_000}"
        return url

    async def _run(self) -> None:
        import websockets

        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self._url(), max_size=2**22) as ws:
                    logger.info(f"jetstream connected for {self.did}")
                    backoff = 1.0
                    async for message in ws:
                        await self._handle(message)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    f"jetstream connection lost: {e}; retry in {backoff:.0f}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX_SECONDS)

    async def _handle(self, message: str | bytes) -> None:
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            return
        if event.get("did") not in (None, self.did):
            await self._handle_watched(event)
            return
        row = event_to_row(event)
        if row is None:
            return
        append_op(row)
        if (
            self.on_post is not None
            and row["nsid"] == "app.bsky.feed.post"
            and row["op"] in ("create", "update")
            and row["record"] is not None
        ):
            try:
                await self.on_post(row)
            except Exception as e:
                logger.warning(f"own-post index hook failed for {row['rkey']}: {e}")

    def is_own_pull(self, uri: str) -> bool:
        return uri.startswith(f"at://{self.did}/sh.tangled.repo.pull/")

    async def _handle_watched(self, event: dict[str, Any]) -> None:
        """An event from a watched repo. Only pull-request comments on phi's
        own pulls matter; everything else from those repos is ignored."""
        if self.on_pull_comment is None or event.get("kind") != "commit":
            return
        commit = event.get("commit") or {}
        if (
            commit.get("collection") != PULL_COMMENT_NSID
            or commit.get("operation") != "create"
        ):
            return
        record = commit.get("record") or {}
        if not self.is_own_pull(str(record.get("pull") or "")):
            return
        # jetstream resumes from phi's own last op, which can be hours old
        # when she has been quiet; without this, every reconnect would
        # replay the same review comment and wake her again.
        time_us = int(event.get("time_us") or 0)
        if time_us and time_us <= _watched_cursor():
            return
        if time_us:
            _set_watched_cursor(time_us)
        try:
            await self.on_pull_comment(str(event.get("did")), record)
        except Exception as e:
            logger.warning(f"pull-comment wake failed: {e}")
