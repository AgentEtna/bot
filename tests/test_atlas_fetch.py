"""Regression tests for bot/core/atlas.py — the read side of the atlas.

The fetch path is two requests: get_record for the small header, then
sync.getBlob (via httpx) for the JSON blob. These tests stub both with
fakes so we can exercise the caching + error paths without hitting PDS.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.core import atlas as atlas_module


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with an empty atlas cache."""
    atlas_module._cached_record_cid = None
    atlas_module._cached_atlas = None
    yield
    atlas_module._cached_record_cid = None
    atlas_module._cached_atlas = None


def _record_obj(*, uri: str, cid: str, blob_cid: str, point_count: int = 100):
    """A minimal stand-in for the atproto get_record response object."""
    return SimpleNamespace(
        uri=uri,
        cid=cid,
        value={
            "generatedAt": "2026-05-14T05:48:00Z",
            "pointCount": point_count,
            "blob": {
                "ref": {"$link": blob_cid},
                "mimeType": "application/octet-stream",
            },
        },
    )


def _make_atlas_bytes(point_count: int = 100) -> bytes:
    return json.dumps(
        {
            "generated_at": "2026-05-14T05:48:00Z",
            "embedding_model": "text-embedding-3-small",
            "reducer": "umap",
            "clusterer": "hdbscan",
            "point_count": point_count,
            "clusters_coarse": [],
            "clusters_fine": [],
            "points": [],
        }
    ).encode("utf-8")


async def test_get_atlas_returns_none_when_no_record():
    """No atlas yet → return None gracefully (endpoint surfaces 404)."""
    with patch.object(atlas_module, "_fetch_record", new=AsyncMock(return_value=None)):
        result = await atlas_module.get_atlas()
    assert result is None


async def test_get_atlas_fetches_and_parses_blob():
    """Happy path: record points at a blob, we fetch + parse it."""
    record = {
        "uri": "at://did:plc:65sucjiel52gefhcdcypynsr/io.zzstoatzz.phi.atlas/self",
        "cid": "bafyrecord1",
        "value": _record_obj(uri="x", cid="x", blob_cid="bafyblob1").value,
    }
    blob_bytes = _make_atlas_bytes(point_count=2747)
    with (
        patch.object(atlas_module, "_fetch_record", new=AsyncMock(return_value=record)),
        patch.object(
            atlas_module, "_fetch_blob", new=AsyncMock(return_value=blob_bytes)
        ),
    ):
        result = await atlas_module.get_atlas()

    assert result is not None
    assert result["point_count"] == 2747
    assert result["embedding_model"] == "text-embedding-3-small"


async def test_get_atlas_caches_by_record_cid():
    """Same record CID → cached parse, no second blob fetch."""
    record = {
        "uri": "at://...",
        "cid": "bafyrecord1",
        "value": _record_obj(uri="x", cid="x", blob_cid="bafyblob1").value,
    }
    blob_bytes = _make_atlas_bytes()
    fetch_blob = AsyncMock(return_value=blob_bytes)
    fetch_record = AsyncMock(return_value=record)
    with (
        patch.object(atlas_module, "_fetch_record", new=fetch_record),
        patch.object(atlas_module, "_fetch_blob", new=fetch_blob),
    ):
        await atlas_module.get_atlas()
        await atlas_module.get_atlas()
        await atlas_module.get_atlas()

    # record is checked every time (cheap), but blob only fetched once
    assert fetch_record.call_count == 3
    assert fetch_blob.call_count == 1


async def test_get_atlas_refetches_when_record_cid_changes():
    """New atlas written by prefect → new record CID → blob re-fetched + re-parsed."""
    record_v1 = {
        "uri": "at://...",
        "cid": "bafyrecord1",
        "value": _record_obj(uri="x", cid="x", blob_cid="bafyblob1").value,
    }
    record_v2 = {
        "uri": "at://...",
        "cid": "bafyrecord2",
        "value": _record_obj(uri="x", cid="x", blob_cid="bafyblob2").value,
    }
    fetch_blob = AsyncMock(side_effect=[_make_atlas_bytes(100), _make_atlas_bytes(200)])
    fetch_record = AsyncMock(side_effect=[record_v1, record_v2])
    with (
        patch.object(atlas_module, "_fetch_record", new=fetch_record),
        patch.object(atlas_module, "_fetch_blob", new=fetch_blob),
    ):
        r1 = await atlas_module.get_atlas()
        r2 = await atlas_module.get_atlas()

    assert r1 is not None and r1["point_count"] == 100
    assert r2 is not None and r2["point_count"] == 200
    assert fetch_blob.call_count == 2


async def test_get_atlas_handles_record_with_no_blob_ref():
    """Malformed record (no blob) → return None, not crash."""
    bad_record = {
        "uri": "at://...",
        "cid": "bafy",
        "value": {"generatedAt": "x", "pointCount": 0, "blob": {}},
    }
    with patch.object(
        atlas_module, "_fetch_record", new=AsyncMock(return_value=bad_record)
    ):
        result = await atlas_module.get_atlas()
    assert result is None


async def test_get_atlas_handles_invalid_json_blob():
    """Blob isn't valid JSON → return None, not crash."""
    record = {
        "uri": "at://...",
        "cid": "bafyrecord",
        "value": _record_obj(uri="x", cid="x", blob_cid="bafyblob").value,
    }
    with (
        patch.object(atlas_module, "_fetch_record", new=AsyncMock(return_value=record)),
        patch.object(
            atlas_module,
            "_fetch_blob",
            new=AsyncMock(return_value=b"this is not json {{{"),
        ),
    ):
        result = await atlas_module.get_atlas()
    assert result is None
