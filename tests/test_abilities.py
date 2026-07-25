"""A tool without a risk declaration is not a valid tool.

The point of these tests is the bijection: every registered tool has a
declaration, and every declaration names a real tool. Enforced in both
directions, a declaration cannot drift from the code — which is the failure
`/api/abilities` was built to fix once already, when the cockpit's
hand-curated `abilities.ts` fell out of sync with what phi could do.

The shape itself is defined by `lexicons/io/zzstoatzz/phi/getAbilities.json`;
these tests hold the code to that lexicon.
"""

import json
from pathlib import Path

import pytest

from bot.core.abilities import ORDER, RISK, at_least, describe, risk_of

LEXICON = json.loads(Path("lexicons/io/zzstoatzz/phi/getAbilities.json").read_text())


def registered_tool_names() -> set[str]:
    """Tool names as actually registered on the agent.

    Read from the source rather than by constructing a PhiAgent, which needs
    live credentials — the decorator is the registration, so the source is
    the truth.
    """
    import re

    names: set[str] = set()
    for path in Path("src/bot/tools").glob("*.py"):
        src = path.read_text()
        for m in re.finditer(r"@agent\.tool[^\n]*\n\s*(?:async\s+)?def\s+(\w+)", src):
            names.add(m.group(1))
    return names


# --- the bijection ---------------------------------------------------------


def test_every_registered_tool_declares_its_risk():
    undeclared = registered_tool_names() - set(RISK)
    assert not undeclared, (
        f"tools registered with no risk declaration: {sorted(undeclared)}. "
        "add an entry to bot/core/abilities.py — a tool without one is not a valid tool."
    )


def test_every_declaration_names_a_real_tool():
    """Catches the other direction: a tool gets deleted and its declaration
    lingers, quietly describing a capability phi no longer has."""
    orphaned = set(RISK) - registered_tool_names()
    assert not orphaned, (
        f"risk declared for tools that no longer exist: {sorted(orphaned)}"
    )


# --- the declarations hold to the lexicon ----------------------------------


def test_magnitudes_are_the_lexicon_s_known_values():
    known = LEXICON["defs"]["risk"]["properties"]["magnitude"]["knownValues"]
    assert set(ORDER) == set(known)
    for name, risk in RISK.items():
        assert risk["magnitude"] in known, name


def test_reasons_are_present_and_within_the_lexicon_s_bound():
    limit = LEXICON["defs"]["risk"]["properties"]["reason"]["maxLength"]
    for name, risk in RISK.items():
        reason = risk["reason"]
        assert reason.strip(), f"{name} has an empty reason"
        assert len(reason) <= limit, f"{name} reason exceeds {limit} chars"


def test_a_reason_says_more_than_the_magnitude_does():
    """ "could be risky" is not a reason. Each one has to name a consequence,
    which in practice means it is a sentence rather than a label."""
    for name, risk in RISK.items():
        assert len(risk["reason"].split()) >= 6, f"{name}'s reason is too thin"


def test_the_lexicon_requires_risk_on_every_ability():
    assert "risk" in LEXICON["defs"]["ability"]["required"]
    assert set(LEXICON["defs"]["risk"]["required"]) == {"magnitude", "reason"}


# --- the ordering ----------------------------------------------------------


@pytest.mark.parametrize(
    "tool,expected",
    [
        ("post", "high"),
        ("place_chicken_trade", "high"),
        ("save_memory", "low"),
        ("search_posts", "none"),
    ],
)
def test_representative_magnitudes(tool: str, expected: str):
    declared = risk_of(tool)
    assert declared is not None and declared["magnitude"] == expected


def test_at_least_orders_by_reach_not_frequency():
    assert at_least("post", "high")
    assert at_least("post", "none")
    assert not at_least("search_posts", "low")
    assert not at_least("nonexistent_tool", "none")


def test_spending_real_money_is_always_high():
    """The one line that should never be softened without a conversation."""
    for tool in ("place_chicken_trade", "generate_image"):
        declared = risk_of(tool)
        assert declared is not None and declared["magnitude"] == "high", tool


def test_reads_never_rate_above_none():
    """If a read ever needs a higher rating, it is not a read."""
    for tool in ("search_posts", "read_feed", "get_own_posts", "query_traces"):
        declared = risk_of(tool)
        assert declared is not None and declared["magnitude"] == "none", tool


# --- what the judge gets ---------------------------------------------------


def test_describe_gives_the_judge_magnitude_and_consequence():
    line = describe("post")
    assert "high-risk" in line
    assert "notifications" in line


def test_describe_is_empty_for_an_unknown_tool():
    """MCP tools aren't declared here; the judge just gets nothing extra."""
    assert describe("mcp__pdsx__create_record") == ""
