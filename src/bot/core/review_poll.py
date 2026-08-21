"""Review comments on phi's pull requests, read from the reviewers' PDSes.

Jetstream is the fast path for these (core/ops_log.py watches the reviewer
DIDs), and on 2026-08-21 it failed three different ways in one afternoon:
the pinned instance went quiet while connected, a sibling instance never
carried the event, and a resumed cursor landed past it. A review comment
is a record in the reviewer's repo; their PDS is the authority and answers
a listRecords in milliseconds. This polls it. Both paths share the handled
set, so a comment wakes phi exactly once whichever path sees it first.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from bot.core.ops_log import FEED_COMMENT_NSID, comment_target

logger = logging.getLogger("bot.review_poll")

HANDLED_FILE = Path("/data/review_comments_handled.json")
_HANDLED_KEEP = 200
_LOOKBACK_SECONDS = 3 * 60 * 60

_pds_cache: dict[str, str] = {}
_started_at = time.time()


def _load() -> list[str]:
    try:
        return list(json.loads(HANDLED_FILE.read_text())["handled"])
    except Exception:
        return []


def was_handled(uri: str) -> bool:
    return uri in _load()


def mark_handled(uri: str) -> None:
    handled = [u for u in _load() if u != uri] + [uri]
    try:
        HANDLED_FILE.parent.mkdir(parents=True, exist_ok=True)
        HANDLED_FILE.write_text(json.dumps({"handled": handled[-_HANDLED_KEEP:]}))
    except Exception as e:
        logger.warning(f"failed to persist handled review comments: {e}")


async def resolve_pds(did: str, http: httpx.AsyncClient) -> str | None:
    if did in _pds_cache:
        return _pds_cache[did]
    if not did.startswith("did:plc:"):
        return None
    try:
        doc = (await http.get(f"https://plc.directory/{did}", timeout=10)).json()
        for svc in doc.get("service") or []:
            if svc.get("id") == "#atproto_pds":
                _pds_cache[did] = str(svc["serviceEndpoint"])
                return _pds_cache[did]
    except Exception as e:
        logger.debug(f"pds lookup failed for {did}: {e}")
    return None


def _fresh_enough(created_at: str) -> bool:
    """Ignore comments older than the lookback at first sight — a fresh
    deploy must not wake phi for last week's reviews."""
    from datetime import datetime

    try:
        ts = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
    except Exception:
        return True
    return ts >= _started_at - _LOOKBACK_SECONDS


async def new_review_comments(
    phi_did: str, reviewer_dids: tuple[str, ...], http: httpx.AsyncClient
) -> list[dict[str, Any]]:
    """Unhandled comments by the reviewers on phi's pull requests, oldest first."""
    prefix = f"at://{phi_did}/sh.tangled.repo.pull/"
    found: list[dict[str, Any]] = []
    for did in reviewer_dids:
        pds = await resolve_pds(did, http)
        if not pds:
            continue
        try:
            response = await http.get(
                f"{pds}/xrpc/com.atproto.repo.listRecords",
                params={"repo": did, "collection": FEED_COMMENT_NSID, "limit": 10},
                timeout=10,
            )
            records = json.loads(response.text, strict=False).get("records") or []
        except Exception as e:
            logger.debug(f"review poll: listRecords failed for {did}: {e}")
            continue
        for r in records:
            value = r.get("value") or {}
            uri = str(r.get("uri") or "")
            if not comment_target(value).startswith(prefix):
                continue
            if not _fresh_enough(str(value.get("createdAt") or "")):
                continue
            if was_handled(uri):
                continue
            found.append({"uri": uri, "did": did, "record": value})
    found.sort(key=lambda c: str(c["record"].get("createdAt") or ""))
    return found
