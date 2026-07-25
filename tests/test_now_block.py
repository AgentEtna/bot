"""[NOW] — the three clocks phi lives between.

She keeps UTC because her container does; she sits physically in fly's
`ord` region, which is Chicago, the same city as the operator; and the
operator is five or six hours behind her own clock depending on the
season. Those are three different facts and she should hold all of them —
a single UTC timestamp is a reading, not a sense of time.
"""

import os
import re

from bot.agent import _FLY_REGIONS


def test_region_codes_resolve_to_places_phi_can_say():
    assert _FLY_REGIONS["ord"] == "chicago"
    assert all(code.islower() and len(code) == 3 for code in _FLY_REGIONS)


def test_unknown_region_falls_through_to_the_code():
    """Better a bare airport code than a confident wrong city."""
    assert _FLY_REGIONS.get("xyz", "xyz") == "xyz"


def test_where_is_read_from_the_environment_not_assumed(monkeypatch):
    """A region change or a move off fly must surface here rather than
    silently making the line wrong."""
    import inspect

    from bot.agent import PhiAgent

    src = inspect.getsource(PhiAgent.__init__)
    assert 'os.environ.get("FLY_REGION")' in src
    assert 'os.environ.get("FLY_MACHINE_ID")' in src


def test_where_line_is_omitted_off_fly(monkeypatch):
    """Locally there is no fly region; the block should say less rather
    than invent a location."""
    monkeypatch.delenv("FLY_REGION", raising=False)
    assert os.environ.get("FLY_REGION") is None


def test_offset_is_rendered_relative_to_phi(monkeypatch):
    """`-5h from you` is the useful framing — phi converts from her own
    clock, not from an abstract zero."""
    import inspect

    from bot.agent import PhiAgent

    src = inspect.getsource(PhiAgent.__init__)
    assert "h from you" in src
    # and the sign must be explicit, so -5 never reads as 5
    assert re.search(r"\{offset:\+g\}", src)
