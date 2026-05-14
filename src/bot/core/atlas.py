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
from atproto_client.models.utils import get_model_as_dict

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
    """Read the small metadata record (generatedAt + pointCount + blob ref).

    `result.value` is a DotDict — its `.get` is intercepted as attribute
    access and returns None for unknown keys, which downstream blows up as
    `None(...)` is not callable. We use the SDK's `get_model_as_dict` to
    deep-convert to plain dicts at the boundary.
    """
    await bot_client.authenticate()
    try:
        result = bot_client.client.com.atproto.repo.get_record(
            {"repo": PHI_DID, "collection": ATLAS_COLLECTION, "rkey": ATLAS_RKEY}
        )
    except Exception as e:
        logger.info(f"no atlas record on PDS yet: {e}")
        return None
    return {
        "uri": result.uri,
        "cid": result.cid,
        "value": get_model_as_dict(result.value),
    }


async def _fetch_blob(blob_cid: str) -> bytes:
    """Fetch the atlas blob via com.atproto.sync.getBlob.

    bsky.social is the entryway; com.atproto.sync.getBlob returns a 302
    redirecting to the actual PDS that holds the blob. follow_redirects=True
    handles that without us having to resolve phi's PDS host ourselves.

    We hit the entryway directly with httpx (rather than the SDK's typed
    wrapper) because raw bytes are simpler when we know we need to parse
    them as JSON regardless of what the response content-type claims.
    """
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
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


# ---------------------------------------------------------------------------
# digest — small text rollup of the atlas
# ---------------------------------------------------------------------------


def _summarize_atlas(atlas: dict[str, Any]) -> str:
    """Compute the digest text from a loaded atlas dict.

    Compact enough to inject into every agent.run as a context block —
    phi can see the shape of her own mind without any tool call. For
    detail, she calls inspect_atlas.
    """
    points: list[dict[str, Any]] = atlas.get("points") or []
    coarse: list[dict[str, Any]] = atlas.get("clusters_coarse") or []
    fine: list[dict[str, Any]] = atlas.get("clusters_fine") or []
    generated_at = atlas.get("generated_at") or "?"
    # truncate ISO timestamp to minutes for readability
    generated_at = generated_at[:16].replace("T", " ") + " UTC"

    # kind distribution, top kinds first
    kinds: dict[str, int] = {}
    for p in points:
        k = p.get("kind") or ""
        if k:
            kinds[k] = kinds.get(k, 0) + 1
    kind_line = ", ".join(
        f"{n} {k}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])
    )

    # promotion distribution
    promo: dict[str, int] = {}
    for p in points:
        s = p.get("promotion_status") or ""
        if s:
            promo[s] = promo.get(s, 0) + 1
    promo_line = " / ".join(
        f"{n} {s}" for s, n in sorted(promo.items(), key=lambda kv: -kv[1])
    )

    # coarse cluster labels with counts (highest count first)
    coarse_sorted = sorted(coarse, key=lambda c: -(c.get("count") or 0))
    coarse_parts = []
    for c in coarse_sorted:
        label = c.get("label") or f"cluster-{c.get('id')}"
        coarse_parts.append(f"{label} ({c.get('count', 0)})")
    coarse_line = ", ".join(coarse_parts)

    return (
        f"[ATLAS — daily map of every point in your mind, generated {generated_at}]\n"
        f"{len(points)} points: {kind_line}\n"
        f"{len(coarse)} coarse clusters: {coarse_line}\n"
        f"{len(fine)} fine clusters\n"
        f"promotion: {promo_line}\n"
        "call inspect_atlas() for the same digest, "
        "inspect_atlas(cluster_id=N) for cluster contents, "
        "inspect_atlas(status='raw') for promotion candidates "
        "(private signals with no public anchor)."
    )


async def get_atlas_digest() -> str:
    """Return the atlas digest, or empty string if no atlas is available.

    Cheap — uses the same record-CID-cached atlas as get_atlas(), and the
    digest itself is recomputed each call (small, microseconds).
    """
    atlas = await get_atlas()
    if atlas is None:
        return ""
    return _summarize_atlas(atlas)
