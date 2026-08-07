

def test_policy_summaries_cover_every_policy():
    """POLICY_SUMMARIES is what phi reads; POLICIES is what the judge
    enforces. dict[PolicySlug, str] doesn't force totality, so a policy
    added without a summary would silently vanish from phi's context."""
    from bot.core.policy import POLICIES, POLICY_SUMMARIES

    assert set(POLICY_SUMMARIES) == set(POLICIES)
    for slug, text in POLICY_SUMMARIES.items():
        assert 0 < len(text) < 200, f"{slug} summary should be one terse line"
