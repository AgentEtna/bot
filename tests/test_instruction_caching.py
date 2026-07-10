"""Regression tests for prompt-cache-friendly instruction handling.

The main agent moved from dynamic system prompts to @agent.instructions so
anthropic_cache_instructions can cache the static personality/rules prefix.
Instructions are re-evaluated on every model request in the tool loop, so
each context block is memoized per run via memoize_per_run — otherwise
network blocks would re-fetch per step and any text change mid-run would
invalidate the message-history cache prefix.
"""

from types import SimpleNamespace
from typing import cast

from pydantic_ai import RunContext

from bot.agent import memoize_per_run
from bot.tools import PhiDeps


def _ctx() -> RunContext[PhiDeps]:
    """A minimal stand-in: memoize_per_run only touches ctx.deps."""
    return cast(
        RunContext[PhiDeps],
        SimpleNamespace(deps=PhiDeps(author_handle="someone")),
    )


async def test_async_block_renders_once_per_run():
    calls = 0

    async def inject_thing() -> str:
        nonlocal calls
        calls += 1
        return f"[THING] render #{calls}"

    block = memoize_per_run(inject_thing)
    ctx = _ctx()
    first = await block(ctx)
    second = await block(ctx)
    assert first == second == "[THING] render #1"
    assert calls == 1


async def test_sync_block_with_ctx_renders_once_per_run():
    calls = 0

    def inject_notifications(ctx) -> str:
        nonlocal calls
        calls += 1
        return f"batch for {ctx.deps.author_handle} #{calls}"

    block = memoize_per_run(inject_notifications)
    ctx = _ctx()
    assert await block(ctx) == await block(ctx) == "batch for someone #1"
    assert calls == 1


async def test_fresh_deps_rerenders():
    calls = 0

    async def inject_now() -> str:
        nonlocal calls
        calls += 1
        return f"[NOW] #{calls}"

    block = memoize_per_run(inject_now)
    assert await block(_ctx()) == "[NOW] #1"
    assert await block(_ctx()) == "[NOW] #2"


async def test_blocks_memoize_independently():
    async def a() -> str:
        return "A"

    async def b() -> str:
        return "B"

    ctx = _ctx()
    assert await memoize_per_run(a)(ctx) == "A"
    assert await memoize_per_run(b)(ctx) == "B"
    assert set(ctx.deps.run_cache) == {a.__qualname__, b.__qualname__}


def test_agent_cache_settings():
    """The main agent caches tool defs + static instructions, and message
    history within the run's tool loop. PhiAgent needs live services to
    instantiate, so assert on the module source."""
    import inspect

    import bot.agent as agent_mod

    src = inspect.getsource(agent_mod)
    assert 'anthropic_cache_tool_definitions="1h"' in src
    assert 'anthropic_cache_instructions="1h"' in src
    assert 'anthropic_cache_messages="5m"' in src
