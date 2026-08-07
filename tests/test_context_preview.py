"""The /diagnostic context preview: stateless render of every block.

Guards the contract main.py's /api/diagnostic/context depends on: every
registered block appears in order after static_instructions, a raising
block reports its error instead of taking the preview down, and nothing
about the call requires a real run.
"""

from bot.agent import PhiAgent


def _phi():
    phi = PhiAgent.__new__(PhiAgent)  # skip __init__ — preview needs 3 attrs
    phi.base_personality = "be kind"
    phi.memory = None
    return phi


async def test_preview_renders_blocks_in_registration_order():
    phi = _phi()

    async def inject_a(ctx) -> str:
        return "[A] alpha"

    async def inject_b(ctx) -> str:
        return ""

    phi.context_blocks = [("inject_a", inject_a), ("inject_b", inject_b)]
    blocks = await phi.render_context_preview()
    assert [b["name"] for b in blocks] == ["static_instructions", "inject_a", "inject_b"]
    assert blocks[0]["text"].startswith("the following is your personality: be kind")
    assert blocks[1]["text"] == "[A] alpha"
    assert blocks[2]["chars"] == 0


async def test_preview_survives_a_raising_block():
    phi = _phi()

    async def inject_bad(ctx) -> str:
        raise RuntimeError("hub is down")

    async def inject_after(ctx) -> str:
        return "[AFTER] still here"

    phi.context_blocks = [("inject_bad", inject_bad), ("inject_after", inject_after)]
    blocks = await phi.render_context_preview()
    bad = blocks[1]
    assert bad["error"] == "RuntimeError: hub is down"
    assert bad["text"] == ""
    assert blocks[2]["text"] == "[AFTER] still here"
