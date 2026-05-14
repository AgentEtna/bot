"""Tests for the omnipresent atlas digest + the inspect_atlas tool.

The digest goes in phi's prompt every call so she always knows the shape
of her own mind. inspect_atlas lets her drill into specific clusters /
points / promotion-status pools when she wants detail.
"""

from unittest.mock import AsyncMock, patch

import pytest

from bot.core import atlas as atlas_module
from bot.core.atlas import _summarize_atlas, get_atlas_digest

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_atlas() -> dict:
    """A small but structurally realistic atlas — three points, two clusters,
    each promotion_status represented at least once."""
    return {
        "generated_at": "2026-05-14T05:48:00Z",
        "embedding_model": "text-embedding-3-small",
        "reducer": "umap",
        "clusterer": "hdbscan",
        "point_count": 5,
        "clusters_coarse": [
            {
                "id": 0,
                "x": 0.0,
                "y": 0.0,
                "count": 3,
                "label": "relays",
                "kind_counts": {"observation": 2, "note": 1},
            },
            {
                "id": 1,
                "x": 1.0,
                "y": 1.0,
                "count": 2,
                "label": "memory",
                "kind_counts": {"observation": 1, "blog": 1},
            },
        ],
        "clusters_fine": [
            {
                "id": 0,
                "x": 0.0,
                "y": 0.0,
                "count": 3,
                "label": "waow relays",
                "kind_counts": {"observation": 2, "note": 1},
                "parent_coarse": 0,
            },
            {
                "id": 1,
                "x": 1.0,
                "y": 1.0,
                "count": 2,
                "label": "memory shape",
                "kind_counts": {"observation": 1, "blog": 1},
                "parent_coarse": 1,
            },
        ],
        "points": [
            {
                "id": "obs-1",
                "kind": "observation",
                "label": "relay.waow.tech dropped to 49%",
                "layer": "private-working",
                "promotion_status": "raw",
                "cluster_coarse": 0,
                "cluster_fine": 0,
                "neighbor_ids": ["obs-2", "note-1"],
                "refs": {"handle": "zzstoatzz.io", "tpuf_id": "abc"},
                "tags": ["relays"],
                "created_at": "2026-05-13T22:00:00Z",
                "x": 0.0,
                "y": 0.0,
            },
            {
                "id": "obs-2",
                "kind": "observation",
                "label": "relay.xero.systems went dead",
                "layer": "private-working",
                "promotion_status": "promoted",
                "cluster_coarse": 0,
                "cluster_fine": 0,
                "neighbor_ids": ["obs-1", "note-1"],
                "refs": {"handle": "zzstoatzz.io"},
                "tags": [],
                "created_at": "2026-05-14T01:00:00Z",
                "x": 0.1,
                "y": 0.1,
            },
            {
                "id": "note-1",
                "kind": "note",
                "label": "waow relay fleet health degraded",
                "layer": "public-knowledge",
                "promotion_status": "promoted",
                "cluster_coarse": 0,
                "cluster_fine": 0,
                "neighbor_ids": ["obs-1", "obs-2"],
                "refs": {"at_uri": "at://x/network.cosmik.card/y"},
                "tags": [],
                "created_at": "2026-05-14T02:00:00Z",
                "x": 0.2,
                "y": 0.0,
            },
            {
                "id": "obs-3",
                "kind": "observation",
                "label": "memory injection is collision not browse",
                "layer": "private-working",
                "promotion_status": "summarized",
                "cluster_coarse": 1,
                "cluster_fine": 1,
                "neighbor_ids": ["blog-1"],
                "refs": {"handle": "zzstoatzz.io"},
                "tags": [],
                "created_at": "2026-05-13T20:00:00Z",
                "x": 1.0,
                "y": 1.0,
            },
            {
                "id": "blog-1",
                "kind": "blog",
                "label": "the shape of it",
                "layer": "public-output",
                "promotion_status": "connected",
                "cluster_coarse": 1,
                "cluster_fine": 1,
                "neighbor_ids": ["obs-3"],
                "refs": {"at_uri": "at://x/app.greengale.document/abc"},
                "tags": ["memory", "epistemology"],
                "created_at": "2026-05-13T21:00:00Z",
                "x": 1.1,
                "y": 1.1,
            },
        ],
    }


@pytest.fixture(autouse=True)
def _reset_cache():
    atlas_module._cached_record_cid = None
    atlas_module._cached_atlas = None
    yield
    atlas_module._cached_record_cid = None
    atlas_module._cached_atlas = None


# ---------------------------------------------------------------------------
# digest
# ---------------------------------------------------------------------------


def test_digest_compact(sample_atlas):
    """Digest is small enough to inject in every prompt."""
    s = _summarize_atlas(sample_atlas)
    # under 1.5KB even with larger atlases — pin a generous ceiling
    assert len(s) < 1500


def test_digest_includes_kind_distribution(sample_atlas):
    s = _summarize_atlas(sample_atlas)
    # kinds appear, ordered by count
    assert "3 observation" in s
    assert "1 note" in s
    assert "1 blog" in s


def test_digest_includes_coarse_cluster_labels(sample_atlas):
    s = _summarize_atlas(sample_atlas)
    assert "relays" in s
    assert "memory" in s


def test_digest_includes_promotion_distribution(sample_atlas):
    s = _summarize_atlas(sample_atlas)
    assert "raw" in s
    assert "promoted" in s
    assert "summarized" in s
    assert "connected" in s


def test_digest_points_phi_at_inspect_atlas(sample_atlas):
    """The digest tells phi the tool exists — otherwise she'd have to guess."""
    s = _summarize_atlas(sample_atlas)
    assert "inspect_atlas" in s


async def test_get_atlas_digest_returns_empty_when_no_atlas():
    with patch.object(atlas_module, "get_atlas", new=AsyncMock(return_value=None)):
        result = await get_atlas_digest()
    assert result == ""


async def test_get_atlas_digest_returns_summary_when_atlas_present(sample_atlas):
    with patch.object(
        atlas_module, "get_atlas", new=AsyncMock(return_value=sample_atlas)
    ):
        result = await get_atlas_digest()
    assert "5 points" in result
    assert "inspect_atlas" in result


# ---------------------------------------------------------------------------
# inspect_atlas tool — exercise the underlying logic directly
# ---------------------------------------------------------------------------


async def _call_inspect(sample_atlas, **kwargs):
    """Invoke the inspect_atlas tool's underlying coroutine, stubbing
    get_atlas to return our fixture."""
    from bot.tools import atlas as atlas_tool

    # build a fake agent that just captures the registered tool
    captured: dict = {}

    class FakeAgent:
        def tool(self, fn):
            captured["fn"] = fn
            return fn

    atlas_tool.register(FakeAgent())
    fn = captured["fn"]

    with (
        patch.object(atlas_tool, "get_atlas", new=AsyncMock(return_value=sample_atlas)),
        patch.object(
            atlas_tool,
            "get_atlas_digest",
            new=AsyncMock(return_value=_summarize_atlas(sample_atlas)),
        ),
    ):
        return await fn(ctx=None, **kwargs)


async def test_inspect_atlas_no_args_returns_digest(sample_atlas):
    out = await _call_inspect(sample_atlas)
    assert "5 points" in out
    assert "inspect_atlas" in out


async def test_inspect_atlas_cluster_id_returns_members(sample_atlas):
    out = await _call_inspect(sample_atlas, cluster_id=0)
    # cluster label, kind composition, members
    assert "waow relays" in out
    assert "3 points total" in out
    assert "obs-1" in out
    assert "note-1" in out
    # member from the other cluster should NOT appear
    assert "obs-3" not in out
    assert "blog-1" not in out


async def test_inspect_atlas_unknown_cluster_id(sample_atlas):
    out = await _call_inspect(sample_atlas, cluster_id=99)
    assert "no points in fine cluster 99" in out


async def test_inspect_atlas_point_id_returns_detail(sample_atlas):
    out = await _call_inspect(sample_atlas, point_id="obs-1")
    assert "id: obs-1" in out
    assert "kind: observation" in out
    assert "layer: private-working" in out
    assert "promotion_status: raw" in out
    # neighbors should resolve to their kind + label
    assert "obs-2" in out
    assert "note-1" in out


async def test_inspect_atlas_unknown_point_id(sample_atlas):
    out = await _call_inspect(sample_atlas, point_id="nonexistent-xyz")
    assert "no point found" in out


async def test_inspect_atlas_status_raw_returns_pressure_pool(sample_atlas):
    out = await _call_inspect(sample_atlas, status="raw")
    # obs-1 is the only 'raw' point in the fixture
    assert "1 points with status 'raw'" in out
    assert "obs-1" in out
    # public points should NOT appear under 'raw'
    assert "obs-2" not in out  # promoted
    assert "note-1" not in out  # promoted


async def test_inspect_atlas_unknown_status(sample_atlas):
    out = await _call_inspect(sample_atlas, status="nonsense")
    assert "unknown status" in out


async def test_inspect_atlas_no_atlas_returns_useful_message(sample_atlas):
    """If the atlas hasn't been generated yet, surface that — don't pretend."""
    from bot.tools import atlas as atlas_tool

    captured: dict = {}

    class FakeAgent:
        def tool(self, fn):
            captured["fn"] = fn
            return fn

    atlas_tool.register(FakeAgent())
    fn = captured["fn"]

    with patch.object(atlas_tool, "get_atlas", new=AsyncMock(return_value=None)):
        out = await fn(ctx=None)
    assert "no atlas" in out
