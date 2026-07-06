"""Tests for the [SEMBLE] public-memory block renderer.

The block replaced a bare-counts summary; these tests pin the shape that
makes it useful — shelf labels with sizes, newest cards first, and empty
output when the library is empty.
"""

from types import SimpleNamespace

from bot.core.public_memory import _card_line, _render


def _rec(uri: str, value: dict) -> SimpleNamespace:
    return SimpleNamespace(uri=uri, value=value)


DID = "did:plc:phi"


def _collection(rkey: str, name: str) -> SimpleNamespace:
    return _rec(
        f"at://{DID}/network.cosmik.collection/{rkey}",
        {"name": name, "description": "d"},
    )


def _link(rkey: str, collection_rkey: str) -> SimpleNamespace:
    return _rec(
        f"at://{DID}/network.cosmik.collectionLink/{rkey}",
        {
            "card": {"uri": f"at://{DID}/network.cosmik.card/{rkey}"},
            "collection": {
                "uri": f"at://{DID}/network.cosmik.collection/{collection_rkey}"
            },
        },
    )


def _url_card(rkey: str, title: str) -> SimpleNamespace:
    return _rec(
        f"at://{DID}/network.cosmik.card/{rkey}",
        {
            "type": "URL",
            "createdAt": "2026-06-24T17:29:30Z",
            "content": {"url": "https://example.com", "metadata": {"title": title}},
        },
    )


def _note_card(rkey: str, text: str) -> SimpleNamespace:
    return _rec(
        f"at://{DID}/network.cosmik.card/{rkey}",
        {
            "kind": "NOTE",
            "createdAt": "2026-07-02T04:30:38Z",
            "content": {"text": text},
        },
    )


def test_render_shows_collections_with_card_counts():
    collections = [_collection("3aaa", "reading list")]
    links = [_link("3lll", "3aaa"), _link("3mmm", "3aaa")]
    block = _render(collections, links, [], 5)
    assert "reading list (2 cards)" in block
    assert "(5 connections)" in block


def test_render_newest_cards_first_by_tid_rkey():
    older = _url_card("3aaa", "older")
    newer = _note_card("3zzz", "newer thought")
    block = _render([], [], [older, newer], 0)
    assert block.index("newer thought") < block.index("older")


def test_render_empty_library_is_empty_block():
    assert _render([], [], [], 0) == ""


def test_card_line_url_uses_title_note_uses_snippet():
    assert "some paper" in _card_line(_url_card("3aaa", "some paper"))
    line = _card_line(_note_card("3bbb", "a  multiline\nthought " + "x" * 200))
    assert "[NOTE]" in line
    assert "\n" not in line
    assert len(line) < 160
