"""[DISCOVERY POOL] selection — narrow when invited, broad when not.

The pool runs ~30 authors upstream. Rendering all of them on a
notifications run spends ~3.5k chars of context on strangers who have
nothing to do with the conversation phi is in. Ranking everywhere would
be worse: similarity to the current topic systematically buries the
strangers who would broaden her, which is the one-topic collapse
docs/patterns.md keeps warning about. So the shape follows the path.
"""

import pytest

from bot.core import discovery_pool

VOCAB = ("atproto", "pds", "magic", "sourdough", "tomato", "chain")


class FakeMemory:
    """Bag-of-words over a tiny vocabulary.

    A binary embedder would tie every off-topic entry at zero, and a top-N
    cut over ties admits whichever happened to be listed first — which
    would let the narrowing test pass without narrowing on relevance.
    """

    def __init__(self):
        self.calls = 0

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        low = text.lower()
        return [1.0 if word in low else 0.0 for word in VOCAB]


def entry(handle: str, *texts: str) -> dict:
    return {
        "handle": handle,
        "did": f"did:plc:{handle}",
        "likes_in_window": 5,
        "last_liked_at": "2026-07-25T00:00:00Z",
        "sample_posts": [
            {"uri": f"at://{handle}/{i}", "text": t} for i, t in enumerate(texts)
        ],
    }


# deliberately larger than RELEVANT_N, so "top N" is an actual cut and not
# just the whole pool wearing a different label
POOL = [
    entry("magician.bsky.social", "street magicians were better in the 90s"),
    entry("zeu.dev", "atproto misconceptions: the PDS is a server you speak to"),
    entry("baker.bsky.social", "sourdough starter finally took"),
    entry("gardener.bsky.social", "the tomatoes are done for"),
    entry("cyclist.bsky.social", "new chain, same hill, still slow"),
]
assert len(POOL) > discovery_pool.RELEVANT_N


@pytest.fixture(autouse=True)
def _pool(monkeypatch):
    async def fake_filtered(memory, top_n=discovery_pool.TOP_N):
        return list(POOL)[:top_n]

    monkeypatch.setattr(discovery_pool, "get_filtered_pool", fake_filtered)
    discovery_pool._block_cache.update({"text": "", "fetched_at": 0.0})
    discovery_pool._vector_cache.clear()


async def test_a_seed_narrows_the_pool_to_relevant_strangers():
    block = await discovery_pool.get_discovery_pool_block(
        None, embedder=FakeMemory(), seed="what do you make of atproto's data model"
    )
    # what the cut guarantees: the pool shrinks to RELEVANT_N, and the
    # on-topic stranger ranks first. which off-topic entries survive the
    # remainder is not asserted — a stub embedder ties every unrelated entry
    # at zero, where real embeddings would order them.
    assert block.count("\n@") == discovery_pool.RELEVANT_N
    assert block.count("\n@") < len(POOL)
    assert "@zeu.dev" in block
    assert block.index("@zeu.dev") < block.index("@magician.bsky.social")


async def test_no_seed_shows_everyone():
    """A scheduled cycle has no conversation to cater to — breadth is the
    point, and it is the path where uninvited-reply fails closed."""
    block = await discovery_pool.get_discovery_pool_block(
        None, embedder=FakeMemory(), seed=""
    )
    for e in POOL:
        assert f"@{e['handle']}" in block
    assert "all of them, so you can look around" in block


async def test_unseeded_block_is_smaller_per_author_than_seeded():
    """Breadth is paid for with fewer samples each, so showing everyone
    doesn't cost more than showing a few in depth."""
    wide = entry("chatty.bsky.social", "one", "two", "the longest sample")
    POOL.append(wide)
    try:
        unseeded = await discovery_pool.get_discovery_pool_block(
            None, embedder=FakeMemory(), seed=""
        )
        # one sample per author on the broad path — and it's the most
        # substantive one, not the most recently liked
        assert unseeded.count("'the longest sample'") == 1
        assert "'one'" not in unseeded and "'two'" not in unseeded
    finally:
        POOL.pop()


async def test_entry_vectors_are_cached_across_batches():
    """A stable pool costs one embedding per batch (the seed), not one per
    stranger — the notifications path runs constantly."""
    memory = FakeMemory()
    await discovery_pool.get_discovery_pool_block(None, embedder=memory, seed="atproto")
    after_first = memory.calls
    await discovery_pool.get_discovery_pool_block(
        None, embedder=memory, seed="atproto again"
    )
    assert memory.calls == after_first + 1


async def test_ranking_failure_falls_back_to_the_whole_pool():
    """Ranking is an optimization. Losing it costs tokens, not the run."""

    class Broken(FakeMemory):
        async def embed(self, text: str):
            raise RuntimeError("embeddings down")

    block = await discovery_pool.get_discovery_pool_block(
        None, embedder=Broken(), seed="atproto"
    )
    assert "@zeu.dev" in block and "@baker.bsky.social" in block


async def test_no_embedder_means_no_ranking_not_an_error():
    """Memory unavailable (no turbopuffer key) must degrade to the full
    pool, not to an empty block."""
    block = await discovery_pool.get_discovery_pool_block(None, seed="atproto")
    assert "@zeu.dev" in block and "@baker.bsky.social" in block


async def test_the_block_permits_reading_as_writing():
    """phi's context is otherwise sealed against exemplars; these samples
    are nearly the only human writing she sees. The header names them as
    writing without a blanket "do not copy their phrasing"."""
    block = await discovery_pool.get_discovery_pool_block(None, seed="")
    assert "their real writing" in block
    assert "do not copy their phrasing" not in block


async def test_the_real_protections_survive():
    """Loosening absorption must not loosen attribution or lifting."""
    block = await discovery_pool.get_discovery_pool_block(None, seed="")
    assert "don't lift anyone's sentences" in block
    assert "attribute" in block


async def test_the_pool_is_framed_as_taste_not_only_leads():
    """These are posts the operator chose to like — the clearest read phi
    gets on his taste, framed without naming a single stylistic rule."""
    block = await discovery_pool.get_discovery_pool_block(None, seed="")
    assert "read you get on his taste" in block


async def test_no_style_coaching_in_the_header():
    """Prescribing a register has been reverted four times (61bf9f8,
    7bb6cd2, 4a88145, 3ca6984), and the alternative — a ~350-char essay on
    how humor carries a point — was coaching prose billed on every run
    (removed 2026-08-07, operator call: wrong artifact, wrong place). The
    header states what the block is and the two hard rules, nothing about
    how to write."""
    block = await discovery_pool.get_discovery_pool_block(None, seed="")
    assert "humor" not in block.lower()
    for prescription in ("be funny", "be witty", "use humor", "make a joke"):
        assert prescription not in block.lower()


def test_best_samples_prefer_substance_over_recency():
    """like-recency order surfaced reply banter ('hi', 'obvs') as the read
    on a person; the sample shown should be the post that shows why the
    operator rates them."""
    from bot.core.discovery_pool import _best_samples

    posts = [
        {"uri": "a", "text": "hi", "liked_at": "2026-08-07"},
        {"uri": "b", "text": "a long substantive post about atproto lexicons and why they matter", "liked_at": "2026-08-05"},
        {"uri": "c", "text": "", "liked_at": "2026-08-06"},
    ]
    best = _best_samples(posts, 1)
    assert len(best) == 1
    assert best[0]["uri"] == "b"


def test_render_is_compact_and_essay_free():
    from bot.core.discovery_pool import _render

    entries = [
        {
            "handle": "someone.bsky.social",
            "did": "did:plc:x",
            "likes_in_window": 7,
            "last_liked_at": "2026-08-06T12:00:00Z",
            "sample_posts": [{"uri": "u", "text": "a real post", "liked_at": ""}],
        }
    ]
    block = _render(entries, ranked=False, samples=1)
    assert "@someone.bsky.social ×7 (08-06)" in block
    assert "humor" not in block
    assert "likes from operator" not in block
    assert "'a real post'" in block
