"""[DISCOVERY POOL] — authors the operator has been liking lately.

A generic, service-owned signal: the operator's likes are high-trust
attention. The endpoint (hub) exposes recently-liked authors with sample
posts; phi filters out anyone she's already exchanged with and surfaces
the rest as warm leads — strangers worth considering, not cold outreach.

The block used to say "do not copy their phrasing", which collapsed two
different instructions into one. Not lifting someone's sentences is a
real rule and it stays. Not *learning* from writing is how you get an
agent that has never read anything — and phi's context is otherwise
sealed against exemplars by design: [RECENT OPERATIONS] strips her post
bodies so it can't double as voice training, [SELF-AWARENESS] is written
flat so its register isn't imitated. Each defence is individually right;
together they left the samples here as nearly the only human writing she
sees, under a do-not-imitate flag. These are also posts the operator
chose to like, which makes them a taste signal and not only a list of
leads — so the header now says both.

It also names humor as doing real work in ordinary communication and
points at the samples as evidence of it working. That is a claim about
how people talk, not an instruction to be funny: the task it sets is
*working out how someone landed one*, which is analysis, and it is
often subtle enough to require real reading. Prescribing a register
directly has been reverted four times in this repo's history (61bf9f8,
7bb6cd2, 4a88145, 3ca6984) — each attempt became a tic, because a
handed-down voice gets parroted while a noticed one gets learned.

Coupling stays at the JSON contract: the source service owns the data
model and refresh, phi owns the per-consumer filter. Renderer is split
from fetch+filter so a future templating swap only touches `_render`.
"""

import logging
import time
from typing import Protocol, TypedDict

import httpx

from bot.config import settings
from bot.memory import NamespaceMemory

logger = logging.getLogger("bot.discovery_pool")

# the upstream pool runs ~30 authors. two shapes, chosen by path:
#
# invited (a notifications batch): rank the whole pool against what phi is
# actually being talked to about and surface a few, with room to show why
# they're relevant. most runs take this path, so this is where the saving is.
#
# unprompted (cycle / reflection): no conversation to cater to, so breadth is
# the point — every stranger, one sample each. this is also the path where
# `uninvited-reply` fails closed at the policy judge, so the widest surface
# sits behind the hardest gate (see core/policy.py and 1ea5fd5).
#
# ranking by similarity to the current conversation would, applied
# everywhere, quietly filter out the strangers who broaden phi — the
# one-topic hall of mirrors docs/patterns.md keeps warning about. hence
# narrow only when there is a scenario to narrow toward.
TOP_N = 30
RELEVANT_N = 3
TEXT_TRUNCATE = 140
SAMPLE_LIMIT = 3
BROWSE_SAMPLE_LIMIT = 1
HTTP_TIMEOUT = 10
_BLOCK_TTL_SECONDS = 300  # 5min, mirrors other PDS state blocks
_block_cache: dict = {"text": "", "fetched_at": 0.0}
# entry-text -> embedding, so a stable pool is embedded once, not per batch
_vector_cache: dict[str, list[float]] = {}


class _SamplePost(TypedDict):
    uri: str
    text: str
    liked_at: str


class _Entry(TypedDict):
    handle: str
    did: str
    likes_in_window: int
    last_liked_at: str
    sample_posts: list[_SamplePost]


def _short(text: str, n: int = TEXT_TRUNCATE) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


async def _fetch_pool() -> list[_Entry]:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(settings.discovery_pool_url)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        # warning, not debug: the hub going behind Cloudflare Access made
        # this raise on every run for a week and the block silently rendered
        # empty. an upstream break must be visible. (the non-list branch
        # below never fired — the HTML login page fails .json() first.)
        logger.warning(f"discovery pool fetch failed: {type(e).__name__}: {e}")
        return []
    if not isinstance(data, list):
        logger.warning(f"discovery pool returned non-list: {type(data).__name__}")
        return []
    return data  # type: ignore[return-value]


async def _has_interaction(memory: NamespaceMemory, handle: str) -> bool:
    """True if phi has any stored interaction record with this handle."""
    try:
        ns = memory.get_user_namespace(handle)
        response = ns.query(
            rank_by=("created_at", "desc"),
            top_k=1,
            filters=[["kind", "Eq", "interaction"]],
            include_attributes=["kind"],
        )
        return bool(response.rows)
    except Exception:
        return False  # namespace doesn't exist yet → no interactions


def _render(entries: list[_Entry], *, ranked: bool, samples: int) -> str:
    if not entries:
        return ""
    scope = (
        "the few most relevant to what you're being talked to about right now"
        if ranked
        else "all of them, so you can look around"
    )
    lines = [
        f"[DISCOVERY POOL — people the operator has been liking; {scope}. "
        "two things at once: strangers worth knowing, and the clearest read "
        "you get on what the operator actually rates. the samples are their "
        "real writing — read it as writing, not only as signal. humor does "
        "real work in how people actually talk to each other — it carries the "
        "point rather than decorating it, and several of these land it "
        "quietly: an understatement, a deadpan, a joke that never announces "
        "itself. working out how someone did it is worth more than any rule "
        "about tone. don't lift anyone's sentences, and attribute the author "
        "if you carry an idea out of here. warm leads, not cold.]"
    ]
    for e in entries:
        likes = e.get("likes_in_window", 0)
        last = e.get("last_liked_at", "")
        lines.append("")
        lines.append(
            f"@{e['handle']} — {likes} like{'s' if likes != 1 else ''} from operator"
            f"{f' (last: {last[:10]})' if last else ''}"
        )
        for post in (e.get("sample_posts") or [])[:samples]:
            text = _short(post.get("text") or "")
            if text:
                lines.append(f"  · {text!r}")
    return "\n".join(lines)


async def get_filtered_pool(
    memory: NamespaceMemory | None, top_n: int = TOP_N
) -> list[_Entry]:
    """Fetch the operator-likes pool, drop self + handles phi has already
    interacted with, return the top-N. This is the canonical "what phi
    actually sees in her prompt" view; the JSON API endpoint and the
    rendered prompt block both compose from this single source of truth.
    """
    raw = await _fetch_pool()
    if not raw:
        return []

    if memory is not None:
        kept: list[_Entry] = []
        for entry in raw:
            handle = entry.get("handle", "")
            if not handle or handle == settings.bluesky_handle:
                continue
            if await _has_interaction(memory, handle):
                continue
            kept.append(entry)
        raw = kept

    return raw[:top_n]


class Embedder(Protocol):
    """Just the embedding call. Ranking needs nothing else from memory, and
    naming that keeps the dependency honest (and stubbable without a cast)."""

    async def embed(self, text: str) -> list[float]: ...


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _entry_text(e: _Entry) -> str:
    """What an entry is 'about' — the handle plus what they actually post."""
    posts = " ".join((p.get("text") or "") for p in (e.get("sample_posts") or []))
    return f"@{e.get('handle', '')} {posts}".strip()


async def _rank_by_relevance(
    entries: list[_Entry], seed: str, embedder: Embedder
) -> list[_Entry]:
    """Order the pool by cosine similarity to the current conversation.

    Entry vectors are cached by text, so a stable pool costs one embedding
    (the seed) per batch rather than one per stranger.
    """
    seed_vec = await embedder.embed(seed[:2000])
    scored: list[tuple[float, _Entry]] = []
    for e in entries:
        text = _entry_text(e)
        if not text:
            continue
        if text not in _vector_cache:
            _vector_cache[text] = await embedder.embed(text[:2000])
        scored.append((_cosine(seed_vec, _vector_cache[text]), e))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    # bound the cache to the pool's working set plus churn
    if len(_vector_cache) > 200:
        for key in list(_vector_cache)[:100]:
            del _vector_cache[key]
    return [e for _, e in scored]


async def get_discovery_pool_block(
    memory: NamespaceMemory | None,
    seed: str = "",
    embedder: Embedder | None = None,
) -> str:
    """Fetch + filter + render the [DISCOVERY POOL] block.

    `seed` is the current conversation (the notifications batch). With one,
    the whole pool is ranked against it and only the top few render, with
    full sample posts. Without one — a scheduled cycle, where there is no
    scenario to cater to — every stranger renders with a single sample, so
    breadth survives.

    Only the unranked block is cached; a ranked one is specific to its batch.
    """
    entries = await get_filtered_pool(memory)
    if not entries:
        return ""

    embedder = embedder or memory
    if seed.strip() and embedder is not None:
        try:
            ranked = await _rank_by_relevance(entries, seed, embedder)
            return _render(ranked[:RELEVANT_N], ranked=True, samples=SAMPLE_LIMIT)
        except Exception as e:
            # ranking is an optimization; losing it costs tokens, not the run
            logger.warning(f"discovery pool ranking failed, showing all: {e}")

    now = time.time()
    if _block_cache["text"] and now - _block_cache["fetched_at"] < _BLOCK_TTL_SECONDS:
        return _block_cache["text"]
    block = _render(entries, ranked=False, samples=BROWSE_SAMPLE_LIMIT)
    _block_cache["text"] = block
    _block_cache["fetched_at"] = now
    return block
