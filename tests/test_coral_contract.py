"""Drift enforcement for the coral <-> phi contract.

phi steers coral through two records on her own repo, and coral reads them
with its own validation rules. Those rules live in a different repo, so
nothing but a test keeps this side honest about them.

The failure this exists to prevent already happened. coral capped each
directive list at 32 entries; on 2026-08-15 phi's suppress record reached 33
and coral silently truncated it, so "IEMbot Additional Details Here" never
applied and the bot footer topped the day chart. coral removed the cap on
2026-08-16 (`ef07a7c`: a monotonically growing editorial log behind any fixed
cap fails eventually) — but this repo kept its own 32 in the lexicon and in
the skill for two more days, so phi went on pruning still-justified entries to
stay under a ceiling that no longer existed. Her live record sat at exactly 32.

The lesson these tests encode: a *rejection* limit must be mirrored tightly
(coral drops an oversized entry, so phi must know the real number), while a
*capacity* limit must never be mirrored tightly (coral's is a safety valve
that errors loudly, and phi guessing lower silently loses editorial work).
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
DIRECTIVES = json.loads(
    (ROOT / "lexicons/io/zzstoatzz/phi/entityDirectives.json").read_text()
)
SKILL = (ROOT / "skills/coral-editorial/SKILL.md").read_text()

PROPS = DIRECTIVES["defs"]["main"]["record"]["properties"]

# coral rejects an entry whose text exceeds this rather than truncating it,
# so phi has to know the real number: backend/src/entity_graph.zig
# DIRECTIVE_TEXT_LEN.
CORAL_TEXT_LEN = 64

# coral's table is heap-allocated with a 10k safety valve that errors loudly.
# phi's lists must have room to grow well past the cap that bit us; the exact
# ceiling is a judgment call, but it may never drift back down near 32.
MIN_LIST_HEADROOM = 128


def test_directive_lists_are_not_capped_near_the_cap_that_truncated():
    for name in ("aliases", "suppress"):
        cap = PROPS[name]["maxLength"]
        assert cap >= MIN_LIST_HEADROOM, (
            f"entityDirectives.{name} caps at {cap}. coral removed its 32-entry "
            "cap in ef07a7c because a growing editorial log behind a fixed cap "
            "fails eventually; a low cap here makes phi prune justified entries "
            "to make room. Raise it, don't lower the floor."
        )


def test_directive_text_length_matches_what_coral_rejects():
    """Too high and phi writes entries coral silently drops; too low and she
    self-censors entries coral would have taken."""
    for name, fields in (("aliases", ("from", "to")), ("suppress", ("text",))):
        for field in fields:
            declared = PROPS[name]["items"]["properties"][field]["maxLength"]
            assert declared == CORAL_TEXT_LEN, (
                f"entityDirectives.{name}.{field} declares maxLength "
                f"{declared}, but coral rejects anything over {CORAL_TEXT_LEN} "
                "bytes (entity_graph.zig DIRECTIVE_TEXT_LEN)."
            )


def test_skill_does_not_hand_phi_a_fixed_list_ceiling():
    """The skill is what phi actually reads during an editorial pass, so a
    stale number here binds her even when the lexicon is right."""
    assert "≤32 entries per list" not in SKILL, (
        "the coral-editorial skill still states a 32-entry list cap that coral "
        "removed; this is what pinned phi's suppress record at exactly 32."
    )
    assert "64 bytes" in SKILL, (
        "the skill must still tell phi the per-text byte limit — coral rejects "
        "oversized entries rather than truncating them."
    )


def test_skill_documents_the_routes_the_tool_can_reach():
    """coral_query lets phi read any endpoint; the skill is where she learns
    what each one is for."""
    for route in (
        "/groups/history",
        "/entity-graph",
        "/history/topics",
        "/history/top",
        "/simcluster/",
    ):
        assert route in SKILL, (
            f"coral route {route} is undocumented in the coral-editorial "
            "skill — phi can query it but not interpret it."
        )
