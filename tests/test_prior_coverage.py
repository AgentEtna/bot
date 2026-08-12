"""Prior-coverage recall — the gracekind regression.

On 2026-08-05 22:02 and 2026-08-06 22:02 phi published the same summary of
the same gracekind post, same link, because nothing answered "have I ever
said anything about this?" at the moment the material was perceived. These
tests replay that scenario against the recall path.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from bot.core import prior_coverage
from bot.core.prior_coverage import (
    coverage_note,
    extract_links,
    links_in_text,
    render_coverage,
)

GRACE_LINK = "bsky.app/profile/gracekind.net/post/3msecoumetsz7"

PRIOR_POST = {
    "rkey": "3msejrfxzzn2d",
    "text": (
        'grace (@gracekind.net) on AI safety plans that amount to "we\'ll '
        'outsmart it": you have to rule that out from the premise if the '
        "thing is actually superintelligent. " + GRACE_LINK
    ),
    "links": ["gracekind.net", GRACE_LINK],
    "created_at": "2026-08-05T22:02:51",
    "distance": 0.18,
}


def test_extract_links_reads_facets_and_text():
    value = {
        "text": "see https://gracekind.net/ for more",
        "facets": [
            {
                "features": [
                    {
                        "$type": "app.bsky.richtext.facet#link",
                        "uri": f"https://{GRACE_LINK}",
                    }
                ],
                "index": {},
            }
        ],
    }
    links = extract_links(value)
    assert GRACE_LINK in links
    assert "gracekind.net" in links  # normalized: no scheme, no trailing slash


def test_gracekind_replay_semantic_hit():
    """24h later, the same material must surface the 08-05 post."""
    material = (
        "@gracekind.net: most AI safety plans reduce to \"we'll outsmart it\", "
        "which is question-begging against something defined as smarter than you."
    )
    note = render_coverage([PRIOR_POST], links_in_text(material))
    assert "2026-08-05" in note
    assert "outsmart" in note


def test_gracekind_replay_link_hit_beats_distance():
    """Even if embeddings drifted apart, the shared link alone surfaces it."""
    far = dict(PRIOR_POST, distance=0.9)
    material = f"interesting thread https://{GRACE_LINK}"
    note = render_coverage([far], links_in_text(material))
    assert "SAME LINK" in note
    assert GRACE_LINK in note


def test_distant_unlinked_hits_stay_silent():
    far = dict(PRIOR_POST, distance=0.9, links=[])
    assert render_coverage([far], ["unrelated.example/post"]) == ""


def test_empty_hits_render_nothing():
    assert render_coverage([], ["gracekind.net"]) == ""


async def test_coverage_note_degrades_to_absence_on_failure():
    memory = Mock()
    memory.embed = AsyncMock(side_effect=RuntimeError("openai down"))
    assert await coverage_note(memory, "some material") == ""


async def test_coverage_note_empty_material_short_circuits():
    memory = Mock()
    assert await coverage_note(memory, "   ") == ""
    memory.embed.assert_not_called()
    assert await coverage_note(None, "material") == ""


async def test_index_post_value_skips_empty_text():
    memory = Mock()
    await prior_coverage.index_post_value(memory, "3x", {"text": "  "})
    memory.client.namespace.assert_not_called()


async def test_index_post_value_upserts_by_rkey():
    memory = Mock()
    memory.embed = AsyncMock(return_value=[0.0] * 4)
    ns = memory.client.namespace.return_value
    await prior_coverage.index_post_value(
        memory,
        "3rkey",
        {
            "text": f"covering https://{GRACE_LINK} today",
            "createdAt": "2026-08-05T22:02:51",
            "reply": None,
        },
    )
    row = ns.write.call_args.kwargs["upsert_rows"][0]
    assert row["id"] == "3rkey"
    assert row["is_reply"] is False
    assert GRACE_LINK in row["links"]


def test_watermark_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(prior_coverage, "WATERMARK_FILE", tmp_path / "wm.json")
    assert prior_coverage._read_watermark() == ""
    prior_coverage._write_watermark("3newest")
    assert prior_coverage._read_watermark() == "3newest"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("no links here", []),
        ("https://a.example/x/ and https://a.example/x", ["a.example/x"]),
    ],
)
def test_links_in_text_normalizes_and_dedupes(text, expected):
    assert links_in_text(text) == expected


def test_render_coverage_carries_speech_act_kind():
    from datetime import UTC, datetime, timedelta

    ts = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    hits = [
        {"text": "said in a thread", "links": [], "created_at": ts,
         "distance": 0.2, "is_reply": True},
        {"text": "said out loud", "links": [], "created_at": ts,
         "distance": 0.2, "is_reply": False},
    ]
    block = render_coverage(hits, [])
    assert "reply)" in block and "top-level post)" in block, (
        "recall must say what kind of speech each hit was — "
        "'didn't I just say this' needs in-what-context, not just when"
    )
