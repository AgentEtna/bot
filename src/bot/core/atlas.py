"""Read phi's atlas artifact off her PDS.

The atlas is regenerated daily by the `phi-atlas` Prefect flow (see
my-prefect-server/flows/phi_atlas.py) and written as a blob on phi's
PDS under `io.zzstoatzz.phi.atlas/self`. The PDS record carries a small
header (generatedAt, pointCount, blob ref); the blob carries the full
JSON (points, clusters, lifecycle metadata).

We cache by the PDS record CID, not by clock — the atlas only changes
when prefect writes a new one, so there's no point re-fetching the
~2MB blob on a TTL when the underlying state didn't change.

The blob is uploaded as `application/octet-stream` (carried project
memory: bsky atproto-pds serializes ReadStream objects when the stored
mime is `application/json` — never upload JSON-bearing blobs as JSON).
The bytes ARE valid JSON regardless; we parse them ourselves rather
than trusting the response content-type.
"""

import json
import logging
from typing import Any

import httpx

from bot.core.atproto_client import bot_client

logger = logging.getLogger("bot.core.atlas")

PHI_DID = "did:plc:65sucjiel52gefhcdcypynsr"
PDS_BASE = "https://bsky.social"
ATLAS_COLLECTION = "io.zzstoatzz.phi.atlas"
ATLAS_RKEY = "self"

# In-process cache keyed by the PDS record CID. When the prefect flow writes
# a new atlas, the record gets a new CID; until then the cached blob bytes
# are byte-identical to what's on PDS.
_cached_record_cid: str | None = None
_cached_atlas: dict[str, Any] | None = None


async def _fetch_record() -> dict[str, Any] | None:
    """Read the small metadata record (generatedAt + pointCount + blob ref)."""
    await bot_client.authenticate()
    try:
        result = bot_client.client.com.atproto.repo.get_record(
            {"repo": PHI_DID, "collection": ATLAS_COLLECTION, "rkey": ATLAS_RKEY}
        )
    except Exception as e:
        logger.info(f"no atlas record on PDS yet: {e}")
        return None
    return {"uri": result.uri, "cid": result.cid, "value": dict(result.value)}


async def _fetch_blob(blob_cid: str) -> bytes:
    """Fetch the atlas blob via com.atproto.sync.getBlob.

    We hit the entryway directly with httpx — the SDK's typed wrapper would
    also work, but raw bytes are simpler when we already know we need to
    parse them as JSON regardless of what the response content-type claims.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            f"{PDS_BASE}/xrpc/com.atproto.sync.getBlob",
            params={"did": PHI_DID, "cid": blob_cid},
        )
        resp.raise_for_status()
        return resp.content


async def get_atlas() -> dict[str, Any] | None:
    """Return the parsed atlas JSON, or None if no atlas has been written yet.

    Cached by PDS record CID: subsequent calls with the same record reuse
    the parsed JSON. When prefect writes a new atlas the record CID changes
    and we re-fetch + re-parse.
    """
    global _cached_record_cid, _cached_atlas

    record = await _fetch_record()
    if record is None:
        return None

    record_cid = record.get("cid")
    if record_cid and record_cid == _cached_record_cid and _cached_atlas is not None:
        return _cached_atlas

    blob_ref = (record.get("value") or {}).get("blob") or {}
    # the SDK's DotDict for atproto blob refs surfaces the CID under
    # blob.ref.$link, but at this point we have a plain dict from dict()
    blob_cid = ((blob_ref.get("ref") or {}).get("$link")) or blob_ref.get("cid")
    if not blob_cid:
        logger.warning(f"atlas record has no blob ref: {record}")
        return None

    blob_bytes = await _fetch_blob(blob_cid)
    try:
        atlas = json.loads(blob_bytes)
    except json.JSONDecodeError as e:
        logger.warning(f"atlas blob {blob_cid} is not valid JSON: {e}")
        return None

    _cached_record_cid = record_cid
    _cached_atlas = atlas
    return atlas
