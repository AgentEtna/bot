"""Extraction is bounded by the high-water mark, not by a count.

phi, 2026-08-21, on the fix for the first-page namespace listing: "the
'5 newest per namespace = done' detail is the more interesting bug of the
two — the same failure mode, just recursive: bounding 'have I seen this' by
a count instead of a cursor/timestamp always eventually mistakes silence for
closure." get_unprocessed_interactions read the 5 newest per namespace and
process_extraction took 20 overall; the observations it wrote then moved the
mark past everything unread. 178 interactions were lost that way before the
one-off backfill. Now every interaction above the mark is walked, oldest
first, in chunks.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot.agent import EXTRACTION_CHUNK, PhiAgent
from bot.memory.namespace_memory import NamespaceMemory

PREFIX = f"{NamespaceMemory.NAMESPACES['users']}-"


def _row(i: int) -> SimpleNamespace:
    return SimpleNamespace(
        content=f"user: q{i}\nbot: a{i}",
        created_at=f"2026-08-{10 + i:02d}T00:00:00",
        source_uris=[f"at://x/{i}"],
    )


def _memory(n_interactions: int, latest_obs: str) -> NamespaceMemory:
    mem = NamespaceMemory.__new__(NamespaceMemory)
    mem.client = Mock()
    mem._user_namespace_ids = lambda: [f"{PREFIX}zzstoatzz_io"]
    ns = Mock()

    def query(**kw):
        if kw["filters"] == {"kind": ["Eq", "interaction"]}:
            rows = [_row(i) for i in range(n_interactions)][::-1]
            return SimpleNamespace(rows=rows[: kw["top_k"]])
        return SimpleNamespace(rows=[SimpleNamespace(created_at=latest_obs)])

    ns.query = query
    mem.client.namespace = lambda _: ns
    return mem


async def test_every_interaction_above_the_mark_is_returned_oldest_first():
    mem = _memory(n_interactions=12, latest_obs="2026-08-11T12:00:00")
    rows = await mem.get_unprocessed_interactions()
    assert len(rows) == 10
    assert [r["created_at"][:10] for r in rows] == [
        f"2026-08-{d}" for d in range(12, 22)
    ]
    assert all(r["handle"] == "zzstoatzz.io" for r in rows)


async def test_process_extraction_walks_the_backlog_in_chunks():
    agent = PhiAgent.__new__(PhiAgent)
    agent.memory = _memory(n_interactions=2 * EXTRACTION_CHUNK + 3, latest_obs="")
    agent.memory._reconcile_observation = AsyncMock()
    prompts: list[str] = []

    async def run(prompt):
        prompts.append(prompt)
        obs = SimpleNamespace(content="fact", source_uris=[])
        return SimpleNamespace(output=SimpleNamespace(observations=[obs]))

    agent._extraction_agent = SimpleNamespace(run=run)

    stored = await agent.process_extraction()

    assert len(prompts) == 3
    assert stored == 3
    assert "q0\n" in prompts[0] and f"q{EXTRACTION_CHUNK}\n" in prompts[1]
    first_obs = agent.memory._reconcile_observation.await_args_list[0].args[1]
    assert first_obs.source_uris == [f"at://x/{i}" for i in range(EXTRACTION_CHUNK)]
