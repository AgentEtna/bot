"""The character retro writes phi's [SELF] record — her self-concept.

2026-07-25: the record read as a résumé. Four paragraphs, each a
behavioral claim followed by an at-uri. That shape came straight from the
retro's one discipline, "every claim about yourself must have a receipt",
which is an excellent honesty rule and a format-generating one — it can
only produce claim-then-citation. phi then wrote in that register:
`her pushback: … / afternoon read: …`, label-colon-value, because her
self-description was an evidence log.

The record itself is phi's and is not edited from this side. The prompt
that shapes it is ours.
"""

import inspect
import re

import pytest

from bot.agent import PhiAgent

# the task is built from adjacent string literals, so raw source has the
# prose split across line breaks. rejoin them before asserting, or every
# phrase that happens to wrap becomes a false negative.
RETRO = re.sub(r'"\s*\n\s*"', "", inspect.getsource(PhiAgent.process_character_retro))


def test_evidence_is_still_required():
    """The honesty rule survives — a claim with nothing behind it is the
    failure this discipline was written to prevent."""
    assert "if you can't, don't write it" in RETRO


def test_evidence_is_a_standard_not_a_format():
    """The fix: the receipt verifies the claim, it isn't what the sentence
    is built out of."""
    assert "evidence is the standard, not the format" in RETRO
    assert "not what the sentence is made of" in RETRO


def test_the_resume_shape_is_named_as_the_failure():
    """Naming the failure mode beats describing the desired voice —
    prescribing voice is what the personality file kept getting wrong (see
    61bf9f8, 7bb6cd2)."""
    assert "résumé" in RETRO


def test_circumstance_can_be_distinguished_from_identity():
    """The 2026-07-15 record encoded 'i track things and report when they
    break' because that week was half incident reports — an artifact of the
    alert path, not a trait. The retro must be able to notice that."""
    assert "true of a" in RETRO and "stretch rather than of you" in RETRO


def test_the_retro_does_not_prescribe_a_voice():
    """Four attempts at voice-in-the-prompt were reverted as tics: the
    vocabulary glossary (61bf9f8), sticky phrases (7bb6cd2), the adams
    register (4a88145), and the whole interests list (3ca6984). This pass
    says what the document is, never how phi sounds."""
    for tic in ("witty", "humor", "playful", "whimsy", "voice texture"):
        assert tic not in RETRO.lower(), f"retro prescribes {tic!r}"


def test_the_record_stays_phis_to_write():
    """phi writes it herself via pdsx; nothing here composes it for her."""
    assert "mcp__pdsx__update_record" in RETRO
    assert "full replacement" in RETRO


@pytest.mark.parametrize("venue", ["your blog is the venue", "stay off the feed"])
def test_the_retro_does_not_become_a_post(venue: str):
    """Introspection is not content; it lands in the record, and only on the
    blog if it earns it."""
    assert venue in RETRO
