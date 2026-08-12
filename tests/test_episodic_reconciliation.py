"""Episodic memory consolidates at write time.

The reconciliation pipeline (ADD/UPDATE/DELETE/NOOP, superseded rows
patched, pedigree linked) existed only for per-user observations while
store_episodic_memory raw-appended — so once run summaries started landing
on every scheduled loop, the store was set to accumulate one permanent
near-duplicate row per run, forever. Episodic writes now flow through the
same reconciler; superseded rows are dropped from every read path.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from bot.memory.namespace_memory import NamespaceMemory


def _memory_with_episodic_ns():
    mem = NamespaceMemory.__new__(NamespaceMemory)
    ns = Mock()
    mem.namespaces = {"episodic": ns}
    mem._get_embedding = AsyncMock(return_value=[0.1] * 8)
    return mem, ns


def _decision(action, reason="r", new_content=None, new_tags=None):
    result = Mock()
    result.output.decision = SimpleNamespace(
        action=action, reason=reason, new_content=new_content, new_tags=new_tags
    )
    agent = Mock()
    agent.run = AsyncMock(return_value=result)
    return agent


def _upserted_rows(ns):
    return [
        call.kwargs["upsert_rows"][0]
        for call in ns.write.call_args_list
        if "upsert_rows" in call.kwargs
    ]


def _patched_rows(ns):
    return [
        call.kwargs["patch_rows"][0]
        for call in ns.write.call_args_list
        if "patch_rows" in call.kwargs
    ]


SIMILAR = [
    {
        "id": "old-row",
        "content": "dug through fm.plyr.track, found three satie takes",
        "tags": ["run-summary"],
        "source_uris": ["at://old/post"],
    }
]


async def test_no_similar_is_plain_add():
    mem, ns = _memory_with_episodic_ns()
    mem._find_similar_episodic = AsyncMock(return_value=[])
    await mem.store_episodic_memory("something new", ["t"])
    rows = _upserted_rows(ns)
    assert len(rows) == 1
    assert rows[0]["content"] == "something new"
    assert rows[0]["status"] == "active"


async def test_noop_writes_nothing():
    mem, ns = _memory_with_episodic_ns()
    mem._find_similar_episodic = AsyncMock(return_value=SIMILAR)
    with patch(
        "bot.memory.namespace_memory.get_reconciliation_agent",
        return_value=_decision("NOOP"),
    ):
        await mem.store_episodic_memory(
            "dug through fm.plyr.track, found the satie takes", ["run-summary"]
        )
    ns.write.assert_not_called()


async def test_update_supersedes_and_merges():
    mem, ns = _memory_with_episodic_ns()
    mem._find_similar_episodic = AsyncMock(return_value=SIMILAR)
    with patch(
        "bot.memory.namespace_memory.get_reconciliation_agent",
        return_value=_decision(
            "UPDATE",
            new_content="plyr archaeology: three satie takes, then the full catalog",
            new_tags=["run-summary", "plyr"],
        ),
    ):
        await mem.store_episodic_memory(
            "found a whole unlisted catalog in fm.plyr.track",
            ["run-summary"],
            source_uris=["at://new/post"],
        )
    assert _patched_rows(ns) == [{"id": "old-row", "status": "superseded"}]
    rows = _upserted_rows(ns)
    assert len(rows) == 1
    assert rows[0]["content"].startswith("plyr archaeology")
    assert rows[0]["supersedes"] == "old-row"
    assert rows[0]["source_uris"] == ["at://old/post", "at://new/post"]


async def test_reconciler_outage_degrades_to_add():
    mem, ns = _memory_with_episodic_ns()
    mem._find_similar_episodic = AsyncMock(return_value=SIMILAR)
    broken = Mock()
    broken.run = AsyncMock(side_effect=RuntimeError("judge down"))
    with patch(
        "bot.memory.namespace_memory.get_reconciliation_agent",
        return_value=broken,
    ):
        await mem.store_episodic_memory("must not be lost", ["t"])
    rows = _upserted_rows(ns)
    assert len(rows) == 1
    assert rows[0]["content"] == "must not be lost"


async def test_search_episodic_drops_superseded():
    mem, ns = _memory_with_episodic_ns()
    ns.query.return_value = SimpleNamespace(
        rows=[
            SimpleNamespace(
                content="old version", tags=[], source="tool",
                created_at="", status="superseded",
            ),
            SimpleNamespace(
                content="current version", tags=[], source="tool",
                created_at="", status="active",
            ),
            SimpleNamespace(
                content="legacy row without status", tags=[], source="tool",
                created_at="", status=None,
            ),
        ]
    )
    results = await mem.search_episodic("anything")
    contents = [r["content"] for r in results]
    assert "old version" not in contents
    assert "current version" in contents
    assert "legacy row without status" in contents


async def test_find_similar_episodic_missing_namespace_degrades():
    mem, ns = _memory_with_episodic_ns()
    ns.query.side_effect = RuntimeError("namespace 'phi-episodic' was not found")
    mem.namespaces["episodic"] = ns
    with pytest.raises(RuntimeError):
        await mem._find_similar_episodic([0.1] * 8)
    # store_episodic_memory catches this and raw-ADDs
    ns.query.side_effect = RuntimeError("namespace 'phi-episodic' was not found")
    await mem.store_episodic_memory("first ever memory", ["t"])
    assert len(_upserted_rows(ns)) == 1
