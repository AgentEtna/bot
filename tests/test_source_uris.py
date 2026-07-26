"""Tests for memory source-uri citations: model validation, role inference, render."""

from bot.memory.extraction import Observation
from bot.memory.namespace_memory import _citation_tail, _source_role

PHI_DID = "did:plc:65sucjiel52gefhcdcypynsr"
OWNER_DID = "did:plc:xbtmt2zjwlrfegqvch7fboei"
STRANGER_DID = "did:plc:abcdefghijklmnopqrstuvwx"


# --- Observation.source_uris validation ---


def test_observation_default_source_uris_empty():
    obs = Observation(content="x cares about y", tags=["interest"])
    assert obs.source_uris == []


def test_observation_accepts_valid_at_uri():
    obs = Observation(
        content="x cares about y",
        tags=["interest"],
        source_uris=[f"at://{STRANGER_DID}/app.bsky.feed.post/3mjuabmoh2o22"],
    )
    assert len(obs.source_uris) == 1


def test_observation_accepts_multiple_uris():
    obs = Observation(
        content="x cares about y",
        tags=[],
        source_uris=[
            f"at://{STRANGER_DID}/app.bsky.feed.post/3mjuabmoh2o22",
            f"at://{STRANGER_DID}/app.bsky.feed.post/3mjuabmoh2o23",
        ],
    )
    assert len(obs.source_uris) == 2


# --- _source_role match/case classification ---


def test_role_phi_post_with_did_context():
    uri = f"at://{PHI_DID}/app.bsky.feed.post/3mjuabmoh2o22"
    assert _source_role(uri, phi_did=PHI_DID, owner_did=OWNER_DID) == "phi-post"


def test_role_phi_post_without_did_context_falls_to_their_post():
    uri = f"at://{PHI_DID}/app.bsky.feed.post/3mjuabmoh2o22"
    # Without phi_did context, we can't distinguish phi from other authors —
    # falls through to "their-post". This is the documented behavior.
    assert _source_role(uri) == "their-post"


def test_role_operator_liked_with_did_context():
    uri = f"at://{OWNER_DID}/app.bsky.feed.like/3mjuabmoh2o22"
    assert _source_role(uri, phi_did=PHI_DID, owner_did=OWNER_DID) == "operator-liked"


def test_role_their_post():
    uri = f"at://{STRANGER_DID}/app.bsky.feed.post/3mjuabmoh2o22"
    assert _source_role(uri, phi_did=PHI_DID, owner_did=OWNER_DID) == "their-post"


def test_role_essay():
    uri = f"at://{STRANGER_DID}/app.greengale.document/3mjuabmoh2o22"
    assert _source_role(uri) == "essay"


def test_role_card():
    uri = f"at://{STRANGER_DID}/network.cosmik.card/3mjuabmoh2o22"
    assert _source_role(uri) == "card"


def test_role_liked_by_other():
    uri = f"at://{STRANGER_DID}/app.bsky.feed.like/3mjuabmoh2o22"
    assert _source_role(uri, owner_did=OWNER_DID) == "liked-by-other"


def test_role_other_collection():
    uri = f"at://{STRANGER_DID}/com.example.unknown/abc"
    assert _source_role(uri) == "other"


def test_role_invalid_uri():
    assert _source_role("not a uri") == "unknown"


def test_role_empty_string():
    assert _source_role("") == "unknown"


# --- _citation_tail formatting ---


def test_tail_empty_returns_empty():
    assert _citation_tail([]) == ""


def test_tail_singular():
    assert _citation_tail(["at://x/y/z"]) == " (1 source)"


def test_tail_plural():
    uris = ["at://x/y/a", "at://x/y/b", "at://x/y/c"]
    assert _citation_tail(uris) == " (3 sources)"


def test_tail_with_age_only():
    from datetime import UTC, datetime, timedelta

    ts = (datetime.now(UTC) - timedelta(minutes=15)).isoformat()
    out = _citation_tail([], ts)
    assert out.startswith(" (")
    assert "ago" in out
    assert "source" not in out


def test_tail_sources_and_age():
    from datetime import UTC, datetime, timedelta

    ts = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    out = _citation_tail(["at://x/y/a", "at://x/y/b"], ts)
    assert "2 sources" in out
    assert "ago" in out
    assert ", " in out  # comma separator between fields


def test_tail_invalid_age_falls_back_to_sources_only():
    out = _citation_tail(["at://x/y/a"], "not a timestamp")
    assert out == " (1 source)"


# --- links survive the prose stripping -------------------------------------
#
# 2026-07-25: phi posted the same essay link at 14:04 and again at 19:01,
# five hours apart, in near-identical words. [RECENT OPERATIONS] said "post
# text hidden; actions only", so nothing in her context could tell her she
# had already shared it. 41623ce stripped post prose to stop the block
# doubling as voice training, and preserved goal and blog titles for the
# same reason a link is preserved now: it identifies the subject without
# carrying any register.


def test_a_link_in_a_post_is_surfaced():
    from bot.core.recent_operations import _summarize

    line = _summarize(
        "app.bsky.feed.post",
        {
            "text": "finally read it: https://lukekanies.com/writing/building-on-atproto/"
        },
    )
    assert "lukekanies.com/writing/building-on-atproto" in line


def test_facet_links_are_preferred_over_display_text():
    from bot.core.recent_operations import _summarize

    line = _summarize(
        "app.bsky.feed.post",
        {
            "text": "see this",
            "facets": [{"features": [{"uri": "https://example.com/the-real-target"}]}],
        },
    )
    assert "example.com/the-real-target" in line


def test_a_post_with_no_links_says_nothing_extra():
    from bot.core.recent_operations import _summarize

    assert _summarize("app.bsky.feed.post", {"text": "no links here"}) == (
        'top-level post: "no links here"'
    )


def test_a_top_level_post_shows_what_it_said():
    """She has to be able to tell she already covered something.

    41623ce stripped every post body to an action and a char count. The
    mirror it was taking down was [SELF-AWARENESS] — a characterization
    written in her own register — and that one stays flat. A chronological
    log of what she published is a different thing, and without it she
    posted the same essay twice in one day.
    """
    from bot.core.recent_operations import _summarize

    line = _summarize(
        "app.bsky.feed.post", {"text": "finally read kanies' actual essay"}
    )
    assert "finally read kanies' actual essay" in line


def test_replies_stay_summarised():
    """High-volume, half of someone else's conversation, not what she repeats."""
    from bot.core.recent_operations import _summarize

    assert (
        _summarize("app.bsky.feed.post", {"text": "thanks!", "reply": {"root": 1}})
        == "reply (7 chars)"
    )


def test_a_long_post_is_bounded():
    """Enough to recognise a subject, not the feed re-rendered into context."""
    from bot.core.recent_operations import POST_PREVIEW, _summarize

    line = _summarize("app.bsky.feed.post", {"text": "x" * 400})
    assert len(line) < POST_PREVIEW + 60
    assert line.endswith('…"')


def test_links_survive_atproto_model_objects():
    """The regression that took phi down for nine hours on 2026-07-26.

    `_fetch_collection` did `dict(rec.value)`, a shallow conversion, so
    nested facets arrived as atproto model objects rather than dicts.
    `facet.get("features")` raised AttributeError: 'Main' object has no
    attribute 'get', and because the block is a dynamic instruction it
    rendered on every path — every run failed, on every entry point, until
    it was fixed.

    docs/patterns.md has carried this trap since May and my own notes name
    `get_model_as_dict` as the answer. Both fixes are here: the boundary
    deep-converts, and extraction tolerates either shape.
    """
    from bot.core.recent_operations import _links_in

    class Feature:
        uri = "https://example.com/from-a-model"

    class Facet:
        features = [Feature()]

    assert _links_in({"text": "see this", "facets": [Facet()]}) == [
        "example.com/from-a-model"
    ]


def test_a_summary_never_raises_on_a_model_shaped_value():
    """The block renders on every run, so anything it touches must not be
    able to take the whole agent down."""
    from bot.core.recent_operations import _summarize

    class Facet:
        features = None

    assert "top-level post" in _summarize(
        "app.bsky.feed.post", {"text": "hello", "facets": [Facet()]}
    )
