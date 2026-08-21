"""Tests for rich text URL parsing, including bare domain URLs."""

from bot.core.rich_text import parse_urls


def test_full_url():
    facets = parse_urls("check out https://example.com/path")
    assert len(facets) == 1
    assert facets[0]["features"][0]["uri"] == "https://example.com/path"


def test_bare_domain_url():
    facets = parse_urls("check out cnbc.com/2025/markets")
    assert len(facets) == 1
    assert facets[0]["features"][0]["uri"] == "https://cnbc.com/2025/markets"


def test_bare_domain_no_path():
    facets = parse_urls("visit example.com")
    assert len(facets) == 1
    assert facets[0]["features"][0]["uri"] == "https://example.com"


def test_full_url_not_duplicated():
    """Full https:// URL should produce exactly one facet, not a bare URL duplicate."""
    facets = parse_urls("see https://cnbc.com/path for details")
    assert len(facets) == 1
    assert facets[0]["features"][0]["uri"] == "https://cnbc.com/path"


def test_mixed_full_and_bare():
    facets = parse_urls("https://a.com and also b.org/page")
    assert len(facets) == 2
    uris = {f["features"][0]["uri"] for f in facets}
    assert uris == {"https://a.com", "https://b.org/page"}


def test_byte_positions_bare_url():
    text = "see cnbc.com/path ok"
    facets = parse_urls(text)
    assert len(facets) == 1
    start = facets[0]["index"]["byteStart"]
    end = facets[0]["index"]["byteEnd"]
    assert text.encode("UTF-8")[start:end] == b"cnbc.com/path"


class TestLinkBoundaries:
    """2026-08-21: phi posted her lexidraw scene as
    https://lexidraw.app/#atproto=<did>,<rkey> and the link facet stopped at
    the comma, so the link opened the viewer with no scene — the operator
    saw her 08-12 drawing instead of the new one. The same post linkified
    `docs/memory.md` as the domain memory.md."""

    def test_comma_inside_fragment_is_kept(self):
        url = "https://lexidraw.app/#atproto=did:plc:65sucjiel52gefhcdcypynsr,3mtl6z7ar7r2m"
        facets = parse_urls(f"scene: {url}")
        assert [f["features"][0]["uri"] for f in facets] == [url]

    def test_trailing_comma_is_punctuation(self):
        facets = parse_urls("see https://example.com/a, then go")
        assert facets[0]["features"][0]["uri"] == "https://example.com/a"

    def test_file_path_is_not_a_domain(self):
        assert parse_urls("read docs/memory.md and the diagrams") == []

    def test_handle_is_not_a_link(self):
        assert parse_urls("thanks @phi.zzstoatzz.io for the reply") == []

    def test_bare_domain_still_links_at_token_start(self):
        facets = parse_urls("(see lexidraw.app/#atproto=x,y) later")
        assert facets[0]["features"][0]["uri"] == "https://lexidraw.app/#atproto=x,y"
