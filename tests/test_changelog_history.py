"""phi can read why she changed, not just that she changed.

`check_infra(aspect="changelog")` returned `message.split("\\n")[0]` — the
subject line of at most 30 commits. The subject says what changed; the body
says why, and the why is the part that cannot be reconstructed from a diff.
Neither that nor the tangled MCP's `commit_log` (also subjects only) could
tell her anything about her own development.

Asked to write her own history, she would have been guessing.
"""

from bot.tools.bluesky import CHANGELOG_CHAR_BUDGET, COMMIT_BODY_LIMIT


def test_a_body_survives_truncation_long_enough_to_carry_reasoning():
    """phi's own commit messages run 1000-1600 chars. A limit that cut them
    to a couple of lines would preserve the subject and lose the point."""
    assert COMMIT_BODY_LIMIT >= 1200


def test_the_response_is_budgeted():
    """A hundred full messages would crowd out everything else in context,
    so a wide window degrades into "narrow it" rather than flooding."""
    assert CHANGELOG_CHAR_BUDGET <= 32000
    assert CHANGELOG_CHAR_BUDGET >= COMMIT_BODY_LIMIT * 5


def test_changelog_is_declared_and_still_a_read():
    """The chunk-1 contract: every tool declares its risk. Returning more
    does not make it a mutation."""
    from bot.core.abilities import risk_of

    declared = risk_of("check_infra")
    assert declared is not None
    assert declared["magnitude"] == "none"
    assert "commit history" in declared["reason"]


def test_the_aspect_description_says_where_reasoning_lives():
    """Guidance about a tool belongs in the tool, not in a task prompt."""
    import inspect

    import bot.tools.bluesky as mod

    src = inspect.getsource(mod)
    assert "where the reasoning for a change lives" in src
    assert "since/until" in src
