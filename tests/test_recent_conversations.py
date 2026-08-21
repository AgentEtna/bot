"""[RECENT CONVERSATIONS] — the stale-botnana regression.

phi reported on 2026-08-20 that the block kept re-presenting two botnana
threads from 07-22 as if still open; she had re-verified them via search_memory
five times. Two defects, both here:

1. turbopuffer lists namespaces 100 per page and the readers took only
   ``page.namespaces`` from the first page — 167 user namespaces, cut at
   "museical", so the operator, the devlog, and every n–z handle were invisible
   to recall *and* observation extraction. botnana (b) was on page 1.
2. the render cut each row at 150 chars (inside the user's half) with no date,
   so an answered month-old exchange read as an undated open question.
"""

from types import SimpleNamespace
from unittest.mock import Mock

from bot.agent import render_recent_conversations
from bot.memory.namespace_memory import NamespaceMemory


class _Page:
    """Mimics turbopuffer's SyncCursorPage: ``.namespaces`` is one page,
    iteration paginates through everything."""

    def __init__(self, ids: list[str], page_size: int = 100):
        self.namespaces = [SimpleNamespace(id=i) for i in ids[:page_size]]
        self._all = [SimpleNamespace(id=i) for i in ids]

    def __iter__(self):
        return iter(self._all)


def _mem_with_namespaces(ids: list[str]) -> NamespaceMemory:
    mem = NamespaceMemory.__new__(NamespaceMemory)
    mem.client = Mock()
    mem.client.namespaces = Mock(return_value=_Page(ids))
    return mem


def test_user_namespace_ids_spans_every_page():
    prefix = f"{NamespaceMemory.NAMESPACES['users']}-"
    ids = [f"{prefix}h{i:03d}" for i in range(167)]
    mem = _mem_with_namespaces(ids)
    assert len(mem._user_namespace_ids()) == 167
    mem.client.namespaces.assert_called_once_with(prefix=prefix)


async def test_recent_interactions_sees_handles_past_the_first_page():
    prefix = f"{NamespaceMemory.NAMESPACES['users']}-"
    ids = [f"{prefix}a{i:03d}" for i in range(100)] + [f"{prefix}zzstoatzz_io"]
    mem = _mem_with_namespaces(ids)

    def namespace(ns_id):
        ns = Mock()
        if ns_id.endswith("zzstoatzz_io"):
            rows = [
                SimpleNamespace(
                    content="user: hi\nbot: hello", created_at="2026-08-20T10:00:00"
                )
            ]
        else:
            rows = [
                SimpleNamespace(
                    content="user: old\nbot: older", created_at="2026-07-22T10:00:00"
                )
            ]
        ns.query = Mock(return_value=SimpleNamespace(rows=rows))
        return ns

    mem.client.namespace = namespace
    recent = await mem.get_recent_interactions(top_k=3)
    assert recent[0]["handle"] == "zzstoatzz.io"
    assert recent[0]["created_at"].startswith("2026-08-20")


def test_render_shows_date_and_both_halves():
    recent = [
        {
            "handle": "botnana.bsky.social",
            "created_at": "2026-07-22T22:25:13",
            "content": (
                "user: does one clean pass at that margin move your confidence on "
                "12b alone, or do you want to see it repeat a few times before "
                "you'd size a 12b-only trade?\n"
                "bot: one pass moves it a little. i'd want two more before sizing."
            ),
        }
    ]
    block = render_recent_conversations(recent)
    assert "a record, not open threads" in block
    assert "2026-07-22 @botnana.bsky.social" in block
    assert 'you replied "one pass moves it a little' in block
    assert "no reply recorded" not in block


def test_render_marks_missing_reply_instead_of_hiding_it():
    block = render_recent_conversations(
        [{"handle": "x.bsky.social", "created_at": "", "content": "user: hello?"}]
    )
    assert "undated @x.bsky.social" in block
    assert "no reply recorded" in block


def test_render_empty():
    assert "no recent interactions" in render_recent_conversations([])
