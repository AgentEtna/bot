"""Regression tests for cited-post extraction.

When a notification references another bluesky post — via a link facet, a
quote-embed, or a record_with_media embed — the message handler surfaces it
as a structured citation so post(in_reply_to=...) can target it directly. These tests
exercise the extractor that pulls those references out of the post record.
"""

from types import SimpleNamespace

from bot.utils.cited_posts import extract_cited_references, resolve_cited_entry


def _make_link_facet(uri):
    return SimpleNamespace(
        index=SimpleNamespace(byte_start=0, byte_end=10),
        features=[SimpleNamespace(py_type="app.bsky.richtext.facet#link", uri=uri)],
    )


def _make_mention_facet(did):
    return SimpleNamespace(
        index=SimpleNamespace(byte_start=0, byte_end=10),
        features=[SimpleNamespace(py_type="app.bsky.richtext.facet#mention", did=did)],
    )


def _make_record(text="", facets=None, embed=None):
    return SimpleNamespace(text=text, facets=facets, embed=embed)


def test_no_facets_no_embed():
    assert extract_cited_references(_make_record()) == []


def test_link_facet_to_bsky_post():
    """The trigger trace from 2026-05-13 — pdsx-style link facet to a bsky post URL."""
    facet = _make_link_facet(
        "https://bsky.app/profile/deepfates.com.deepfates.com.deepfates.com.deepfates.com.deepfates.com/post/3mlo4lmh3j22k"
    )
    refs = extract_cited_references(_make_record(facets=[facet]))
    assert len(refs) == 1
    assert (
        refs[0]["handle_or_did"]
        == "deepfates.com.deepfates.com.deepfates.com.deepfates.com.deepfates.com"
    )
    assert refs[0]["rkey"] == "3mlo4lmh3j22k"
    assert refs[0]["source"] == "facet"


def test_link_facet_with_did_in_url():
    """bsky URLs can also use DID in the profile slug."""
    facet = _make_link_facet("https://bsky.app/profile/did:plc:abc123/post/abcdef")
    refs = extract_cited_references(_make_record(facets=[facet]))
    assert len(refs) == 1
    assert refs[0]["handle_or_did"] == "did:plc:abc123"
    assert refs[0]["rkey"] == "abcdef"


def test_mention_facet_ignored():
    refs = extract_cited_references(
        _make_record(facets=[_make_mention_facet("did:plc:abc")])
    )
    assert refs == []


def test_non_bsky_link_facet_ignored():
    """Links to non-bsky URLs aren't citations of bsky posts."""
    refs = extract_cited_references(
        _make_record(facets=[_make_link_facet("https://example.com/article")])
    )
    assert refs == []


def test_embed_record_quote_post():
    """Quote post: embed.record.uri is the cited at-uri."""
    embed = SimpleNamespace(
        py_type="app.bsky.embed.record",
        record=SimpleNamespace(uri="at://did:plc:xyz/app.bsky.feed.post/zzz123"),
    )
    refs = extract_cited_references(_make_record(embed=embed))
    assert len(refs) == 1
    assert refs[0]["handle_or_did"] == "did:plc:xyz"
    assert refs[0]["rkey"] == "zzz123"
    assert refs[0]["source"] == "embed"


def test_embed_record_with_media():
    """record_with_media: embed.record.record.uri is the cited at-uri."""
    embed = SimpleNamespace(
        py_type="app.bsky.embed.record_with_media",
        record=SimpleNamespace(
            record=SimpleNamespace(uri="at://did:plc:xyz/app.bsky.feed.post/m1")
        ),
    )
    refs = extract_cited_references(_make_record(embed=embed))
    assert len(refs) == 1
    assert refs[0]["handle_or_did"] == "did:plc:xyz"
    assert refs[0]["rkey"] == "m1"


def test_embed_non_post_collection_ignored():
    """Embed of a non-post record (e.g. a list) shouldn't show as a cited post."""
    embed = SimpleNamespace(
        py_type="app.bsky.embed.record",
        record=SimpleNamespace(uri="at://did:plc:xyz/app.bsky.graph.list/abc"),
    )
    refs = extract_cited_references(_make_record(embed=embed))
    assert refs == []


def test_dedup_across_facet_and_embed():
    """If the same post is both linked and quoted, surface it once."""
    facet = _make_link_facet("https://bsky.app/profile/did:plc:xyz/post/zzz123")
    embed = SimpleNamespace(
        py_type="app.bsky.embed.record",
        record=SimpleNamespace(uri="at://did:plc:xyz/app.bsky.feed.post/zzz123"),
    )
    refs = extract_cited_references(_make_record(facets=[facet], embed=embed))
    assert len(refs) == 1


def test_multiple_distinct_citations():
    facets = [
        _make_link_facet("https://bsky.app/profile/alice.bsky.social/post/aaa"),
        _make_link_facet("https://bsky.app/profile/bob.bsky.social/post/bbb"),
    ]
    refs = extract_cited_references(_make_record(facets=facets))
    assert len(refs) == 2
    assert {r["rkey"] for r in refs} == {"aaa", "bbb"}


async def test_resolved_cited_entry_keeps_image_media():
    async def get_posts(uris):
        return SimpleNamespace(posts=[post])

    image_embed = SimpleNamespace(
        py_type="app.bsky.embed.images#view",
        images=[
            SimpleNamespace(
                alt="green drawing",
                fullsize="https://cdn.bsky.app/img/feed_fullsize/plain/did/x/png",
            )
        ],
    )
    post = SimpleNamespace(
        uri="at://did:plc:xyz/app.bsky.feed.post/img1",
        cid="bafy",
        author=SimpleNamespace(handle="alice.test", did="did:plc:xyz"),
        record=_make_record(text="can you see this"),
        embed=image_embed,
        indexed_at="2026-06-02T12:00:00Z",
    )
    client = SimpleNamespace(
        get_posts=get_posts,
    )

    entry = await resolve_cited_entry(
        client,
        {"handle_or_did": "did:plc:xyz", "rkey": "img1", "source": "embed"},
        "at://did:plc:phi/app.bsky.feed.post/root",
    )

    assert entry is not None
    assert entry["embed_desc"] == "[image: green drawing]"
    assert entry["image_urls"] == [
        "https://cdn.bsky.app/img/feed_fullsize/plain/did/x/png"
    ]
