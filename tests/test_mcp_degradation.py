"""Regression test: an unreachable MCP server degrades to a missing toolset.

2026-07-17: a 403 from the logfire MCP (wrong token kind) killed entire
agent runs at toolset-enter time — the daily reflection died on a toolset
it never needed. _run_agent must drop toolsets that fail to connect and
run with the rest.
"""

from unittest.mock import patch

from bot.agent import PhiAgent


class _GoodToolset:
    label = "good"
    entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *exc):
        return None


class _DeadToolset:
    label = "dead"

    async def __aenter__(self):
        raise RuntimeError("403 Forbidden")

    async def __aexit__(self, *exc):
        return None


async def test_dead_mcp_toolset_does_not_kill_run():
    phi = PhiAgent.__new__(PhiAgent)  # skip __init__ — only _run_agent matters
    good, dead = _GoodToolset(), _DeadToolset()
    seen: dict = {}

    class _FakeResult:
        output = "ran fine"

    async def fake_run(prompt, deps=None, toolsets=None):
        seen["toolsets"] = toolsets
        return _FakeResult()

    phi.agent = type("A", (), {"run": staticmethod(fake_run)})()
    with (
        patch.object(PhiAgent, "_mcp_toolsets", return_value=[dead, good]),
        patch("bot.agent.update_residue_from_run"),
    ):
        out = await phi._run_agent(label="test run", prompt="hi", deps=None)

    assert out == "ran fine"
    assert seen["toolsets"] == [good]
    assert good.entered


class TestQueryTraces:
    """query_traces guards: select-only, columnar rendering."""

    def test_render_columnar(self):
        from bot.tools.traces import _render_columnar

        out = _render_columnar(
            {
                "columns": [
                    {"name": "tool", "values": ["post", "query"]},
                    {"name": "n", "values": [3, 1]},
                ]
            }
        )
        assert out.splitlines() == ["tool | n", "post | 3", "query | 1"]
        assert _render_columnar({"columns": []}) == "no rows"


# --- rejected arguments are not an outage -----------------------------------
#
# 2026-07-25: phi called collections_update(access_type="open"). semble
# replied `Input should be 'OPEN' or 'CLOSED'` — everything she needed to fix
# it. The wrapper relabelled that as "semble is unavailable right now, skip
# library writes this run", discarding the correction and telling her to give
# up on a typo. She retried anyway and hit a real server-side failure, but the
# first one was hers to fix.


def test_a_validation_error_is_reported_as_correctable():
    from bot.core.mcp_guard import _is_correctable

    assert _is_correctable(
        "1 validation error for call[update]\naccess_type\n  "
        "Input should be 'OPEN' or 'CLOSED' [type=literal_error]"
    )


def test_an_opaque_validation_error_is_still_correctable():
    """Even without the field detail, a rejected argument is not an outage."""
    from bot.core.mcp_guard import _is_correctable

    assert _is_correctable("Error calling tool 'collections_update': Validation error")


def test_transport_failures_are_still_outages():
    """The original degradation still has to work — a genuinely unreachable
    semble must not send phi into a retry loop."""
    from bot.core.mcp_guard import _is_correctable

    for outage in (
        "Connection refused",
        "ReadTimeout: timed out",
        "502 Bad Gateway",
        "ClientConnectorError",
    ):
        assert not _is_correctable(outage), outage
