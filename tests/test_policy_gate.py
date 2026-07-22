"""Regression tests for the pre-action policy gate (bot.core.policy +
bot.tools.posting._policy_gate).

Trigger incident (2026-06-30): a model upgrade turned a never-written norm
("don't enter strangers' threads uninvited") into an unprompted reply from a
scheduled cycle. The gate is the actor/judge split that makes the policies
enforceable without hard-coding them.
"""

from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest

from bot.core.policy import PolicySlug, PolicyVerdict
from bot.tools import posting
from bot.tools.posting import _policy_gate, _reply_provenance


def _verdict(
    v: Literal["allow", "warn", "block"],
    policy: PolicySlug | None = None,
    reason: str | None = None,
) -> PolicyVerdict:
    out: PolicyVerdict = {"verdict": v}
    if policy is not None:
        out["policy"] = policy
    if reason is not None:
        out["reason"] = reason
    return out


async def test_block_refuses_and_names_policy():
    with patch.object(
        posting,
        "check_action",
        AsyncMock(return_value=_verdict("block", "uninvited-reply", "nobody asked.")),
    ):
        refusal, note = await _policy_gate("reply to x", "unprompted", unprompted=True)
    assert refusal is not None
    assert "uninvited-reply" in refusal
    assert "nobody asked." in refusal
    assert "nothing was posted" in refusal
    assert note == ""


async def test_warn_passes_with_note():
    with patch.object(
        posting,
        "check_action",
        AsyncMock(return_value=_verdict("warn", "bliss-attractor", "third one today.")),
    ):
        refusal, note = await _policy_gate("post: ...", "top-level", unprompted=True)
    assert refusal is None
    assert "bliss-attractor" in note
    assert "third one today." in note


async def test_allow_is_clean():
    with patch.object(
        posting, "check_action", AsyncMock(return_value=_verdict("allow"))
    ):
        refusal, note = await _policy_gate("post: hi", "invited", unprompted=False)
    assert refusal is None
    assert note == ""


async def test_judge_failure_fails_closed_when_unprompted():
    with patch.object(
        posting, "check_action", AsyncMock(side_effect=RuntimeError("judge down"))
    ):
        refusal, note = await _policy_gate("post: ...", "cycle", unprompted=True)
    assert refusal is not None
    assert "fail-closed" in refusal
    assert note == ""


async def test_judge_failure_fails_open_when_invited():
    with patch.object(
        posting, "check_action", AsyncMock(side_effect=RuntimeError("judge down"))
    ):
        refusal, note = await _policy_gate("reply: ...", "batch", unprompted=False)
    assert refusal is None
    assert note == ""


def test_reply_provenance_batch_is_invited():
    notifs = {
        "at://did:plc:abc/app.bsky.feed.post/1": {
            "author_handle": "pds.dad",
            "reason": "mention",
        }
    }
    p = _reply_provenance("at://did:plc:abc/app.bsky.feed.post/1", notifs)
    assert "invited" in p
    assert "@pds.dad" in p


def test_reply_provenance_out_of_batch_is_unprompted():
    p = _reply_provenance("at://did:plc:stranger/app.bsky.feed.post/1", {})
    assert "unprompted" in p


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestOperatorAuthorizationNote:
    """regression (2026-07-21): the judge blocked the botnana introduction
    minutes after the operator's like authorized it — the like lived only in
    phi's reasoning and never reached the judge's provenance."""

    def test_owner_like_in_batch_surfaces_authorization(self):
        from bot.config import settings
        from bot.tools.posting import _operator_authorization_note

        note = _operator_authorization_note(
            {
                "at://x/app.bsky.feed.post/1": {
                    "reason": "like",
                    "author_handle": settings.owner_handle,
                    "post_text": "like this to authorize tagging @someone",
                }
            }
        )
        assert "operator" in note
        assert "authorize tagging @someone" in note

    def test_stranger_like_is_not_authorization(self):
        from bot.tools.posting import _operator_authorization_note

        assert (
            _operator_authorization_note(
                {
                    "at://x/1": {
                        "reason": "like",
                        "author_handle": "stranger.bsky.social",
                        "post_text": "like this to authorize",
                    },
                    "at://x/2": {
                        "reason": "reply",
                        "author_handle": "zzstoatzz.io",
                        "post_text": "not a like",
                    },
                }
            )
            == ""
        )
