"""Regression tests for bot/core/docket.py — the bot-side projection of
the daily promotion object.

Stubs _fetch_record + _fetch_blob to exercise the cache + digest paths
without hitting PDS. Mirrors test_atlas_fetch.py.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from bot.core import docket as docket_module
from bot.core.docket import _summarize_docket


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with an empty docket cache."""
    docket_module._cached_record_cid = None
    docket_module._cached_docket = None
    yield
    docket_module._cached_record_cid = None
    docket_module._cached_docket = None


def _record(*, cid: str = "bafyrec1", blob_cid: str = "bafyblob1") -> dict:
    return {
        "uri": "at://did:plc:65sucjiel52gefhcdcypynsr/io.zzstoatzz.phi.docket/self",
        "cid": cid,
        "value": {
            "generatedAt": "2026-05-16T13:05:00Z",
            "candidateCount": 7,
            "atlasRecordCid": "bafyatlas1",
            "blob": {
                "ref": {"$link": blob_cid},
                "mimeType": "application/octet-stream",
            },
        },
    }


def _docket_bytes(n: int = 3) -> bytes:
    return json.dumps(
        {
            "generated_at": "2026-05-16T13:05:00Z",
            "atlas_record_cid": "bafyatlas1",
            "atlas_point_count": 2817,
            "candidates": [
                {
                    "id": f"cand-{i}",
                    "title": f"candidate {i}",
                    "rationale": f"rationale for {i}",
                    "private_evidence": [],
                    "existing_public_anchors": [],
                    "related_tags": [],
                    "suggested_shape": "note" if i % 2 == 0 else "card",
                    "atlas_cluster_fine": i,
                    "atlas_cluster_coarse": 0,
                }
                for i in range(n)
            ],
        }
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# get_docket — fetch + cache
# ---------------------------------------------------------------------------


async def test_returns_none_when_no_record():
    with patch.object(docket_module, "_fetch_record", new=AsyncMock(return_value=None)):
        result = await docket_module.get_docket()
    assert result is None


async def test_happy_fetch_and_parse():
    rec = _record()
    blob = _docket_bytes(n=5)
    with (
        patch.object(docket_module, "_fetch_record", new=AsyncMock(return_value=rec)),
        patch.object(docket_module, "_fetch_blob", new=AsyncMock(return_value=blob)),
    ):
        result = await docket_module.get_docket()

    assert result is not None
    assert len(result["candidates"]) == 5
    assert result["candidates"][0]["title"] == "candidate 0"


async def test_caches_by_record_cid():
    """Same record CID → cached parse, no second blob fetch."""
    rec = _record()
    fetch_blob = AsyncMock(return_value=_docket_bytes())
    fetch_record = AsyncMock(return_value=rec)
    with (
        patch.object(docket_module, "_fetch_record", new=fetch_record),
        patch.object(docket_module, "_fetch_blob", new=fetch_blob),
    ):
        await docket_module.get_docket()
        await docket_module.get_docket()
        await docket_module.get_docket()
    assert fetch_record.call_count == 3
    assert fetch_blob.call_count == 1


async def test_refetches_when_record_cid_changes():
    """New docket written by prefect → new record CID → blob re-fetched."""
    rec_v1 = _record(cid="bafyrec1", blob_cid="bafyblob1")
    rec_v2 = _record(cid="bafyrec2", blob_cid="bafyblob2")
    fetch_blob = AsyncMock(side_effect=[_docket_bytes(n=3), _docket_bytes(n=5)])
    fetch_record = AsyncMock(side_effect=[rec_v1, rec_v2])
    with (
        patch.object(docket_module, "_fetch_record", new=fetch_record),
        patch.object(docket_module, "_fetch_blob", new=fetch_blob),
    ):
        r1 = await docket_module.get_docket()
        r2 = await docket_module.get_docket()
    assert r1 is not None and len(r1["candidates"]) == 3
    assert r2 is not None and len(r2["candidates"]) == 5
    assert fetch_blob.call_count == 2


async def test_no_blob_ref_returns_none():
    bad = {
        "uri": "at://...",
        "cid": "bafy",
        "value": {"generatedAt": "x", "candidateCount": 0, "blob": {}},
    }
    with patch.object(docket_module, "_fetch_record", new=AsyncMock(return_value=bad)):
        result = await docket_module.get_docket()
    assert result is None


async def test_invalid_json_blob_returns_none():
    rec = _record()
    with (
        patch.object(docket_module, "_fetch_record", new=AsyncMock(return_value=rec)),
        patch.object(
            docket_module,
            "_fetch_blob",
            new=AsyncMock(return_value=b"not json {{"),
        ),
    ):
        result = await docket_module.get_docket()
    assert result is None


# ---------------------------------------------------------------------------
# _summarize_docket — the tiny prompt block
# ---------------------------------------------------------------------------


def test_digest_compact_with_candidates():
    """Digest stays small even with many candidates."""
    docket = json.loads(_docket_bytes(n=15))
    s = _summarize_docket(docket)
    # generous ceiling; with 15 candidates we should still be well under
    assert len(s) < 2000


def test_digest_includes_only_title_and_shape():
    """No rationale, no evidence — those stay one pdsx fetch away."""
    docket = json.loads(_docket_bytes(n=3))
    # add rationale to verify it's NOT in the digest
    docket["candidates"][0]["rationale"] = "RATIONALE_TEXT_DO_NOT_INCLUDE"
    s = _summarize_docket(docket)
    assert "candidate 0" in s
    assert "[note]" in s
    assert "[card]" in s
    assert "RATIONALE_TEXT_DO_NOT_INCLUDE" not in s


def test_digest_empty_when_no_candidates():
    """No candidates → still emit a useful 'nothing today' message."""
    docket = json.loads(_docket_bytes(n=0))
    s = _summarize_docket(docket)
    assert "no candidates" in s.lower()


def test_digest_points_at_pdsx_for_full_record():
    """The whole point: tell phi where to find the rest."""
    docket = json.loads(_docket_bytes(n=2))
    s = _summarize_docket(docket)
    assert "pdsx" in s.lower() or "get_record" in s


async def test_get_docket_digest_empty_when_no_docket():
    with patch.object(docket_module, "get_docket", new=AsyncMock(return_value=None)):
        result = await docket_module.get_docket_digest()
    assert result == ""


async def test_get_docket_digest_returns_summary_when_present():
    with patch.object(
        docket_module,
        "get_docket",
        new=AsyncMock(return_value=json.loads(_docket_bytes(n=2))),
    ):
        result = await docket_module.get_docket_digest()
    assert "2 candidates today" in result
    assert "candidate 0" in result
