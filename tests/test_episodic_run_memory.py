"""Regression tests: scheduled runs must leave and consult episodic memory.

2026-08-10/11: three musings "discovered" the fm.plyr.track catalog in a row.
The 08-10 dig never called save_memory (episodic writes were voluntary and
scheduled runs never volunteered), and inject_episodic returned "" for any
run without a notification batch, so the 08-11 run had no way to know the
exploration had already happened. Two invariants now:

- _run_agent stores the run summary as an episodic memory for scheduled
  runs (no notifications_context), unconditionally.
- inject_episodic seeds recall from residue on scheduled runs instead of
  going silent.
"""

import inspect
from unittest.mock import AsyncMock, Mock, patch

from bot.agent import PhiAgent
from bot.tools._helpers import PhiDeps


class _FakeResult:
    output = "dug through fm.plyr.track, found three satie takes, posted"


def _phi_with_fake_run():
    phi = PhiAgent.__new__(PhiAgent)  # skip __init__ — only _run_agent matters

    async def fake_run(prompt, deps=None, toolsets=None):
        return _FakeResult()

    phi.agent = type("A", (), {"run": staticmethod(fake_run)})()
    return phi


def _deps(notifications_context=None):
    memory = Mock()
    memory.store_episodic_memory = AsyncMock()
    return PhiDeps(
        author_handle="",
        memory=memory,
        notifications_context=notifications_context,
    )


async def test_scheduled_run_stores_episodic_summary():
    phi = _phi_with_fake_run()
    deps = _deps()
    with (
        patch.object(PhiAgent, "_mcp_toolsets", return_value=[]),
        patch("bot.agent.update_residue_from_run"),
    ):
        out = await phi._run_agent(label="original thought", prompt="hi", deps=deps)

    assert out == _FakeResult.output
    deps.memory.store_episodic_memory.assert_awaited_once()
    content = deps.memory.store_episodic_memory.await_args.args[0]
    assert content == f"original thought: {_FakeResult.output}"
    assert (
        deps.memory.store_episodic_memory.await_args.kwargs["source"]
        == "run:original thought"
    )


async def test_batch_run_does_not_double_store():
    phi = _phi_with_fake_run()
    deps = _deps(notifications_context={"at://x": {"post_text": "hello"}})
    with (
        patch.object(PhiAgent, "_mcp_toolsets", return_value=[]),
        patch("bot.agent.update_residue_from_run"),
    ):
        await phi._run_agent(label="notification batch", prompt="hi", deps=deps)

    deps.memory.store_episodic_memory.assert_not_awaited()


async def test_episodic_store_failure_does_not_kill_run():
    phi = _phi_with_fake_run()
    deps = _deps()
    deps.memory.store_episodic_memory = AsyncMock(side_effect=RuntimeError("tpuf down"))
    with (
        patch.object(PhiAgent, "_mcp_toolsets", return_value=[]),
        patch("bot.agent.update_residue_from_run"),
    ):
        out = await phi._run_agent(label="original thought", prompt="hi", deps=deps)

    assert out == _FakeResult.output


def test_inject_episodic_seeds_from_run_prompt_only():
    """inject_episodic is a closure in __init__; assert its shape at source
    level (the idiom test_now_block uses): recall is keyed to the prompt
    that started the run — never to residue, which amplified whatever was
    already lingering (2026-08-12: months-old prefect logs every slot).
    """
    src = inspect.getsource(PhiAgent.__init__)
    _, _, episodic = src.partition("async def inject_episodic")
    episodic = episodic.split("@_run_scoped")[0]
    assert "run_prompt" in episodic, (
        "scheduled runs must seed episodic recall from the run's own prompt"
    )
    assert "render_residue_block" not in episodic, (
        "residue-seeded recall is back — memory as amplifier, not cue"
    )
