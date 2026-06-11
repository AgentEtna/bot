"""Evals for the skills system — does the agent reach for the right skill
when it doesn't have a dedicated tool for the task?

This eval is intentionally minimal: it verifies that when phi is asked to
do something that lives in a skill's domain (saving a URL to cosmik), she
loads the relevant skill before acting. The "she actually constructs and
sends a valid record" question is downstream of the skill-loading
question; if she doesn't load the skill, no construction will work.
"""

import os
from collections import defaultdict
from pathlib import Path

import pytest
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai_skills import SkillsToolset

from bot.config import Settings


class Response(BaseModel):
    action: str = Field(description="reply, like, repost, post, save, or ignore")
    text: str | None = None


class _ToolCallSpy:
    def __init__(self):
        self.calls: dict[str, list[dict]] = defaultdict(list)

    def record(self, name: str, **kwargs):
        self.calls[name].append(kwargs)

    def was_called(self, name: str) -> bool:
        return len(self.calls[name]) > 0

    def reset(self):
        self.calls.clear()


_spy = _ToolCallSpy()


@pytest.fixture(scope="session")
def settings():
    return Settings()


@pytest.fixture(scope="session")
def skills_agent(settings):
    """Agent with the real SkillsToolset and mocked pdsx record creation.

    The SkillsToolset points at the real bot/skills/ directory so phi
    sees actual skill descriptions in the always-loaded preamble. The
    mocked pdsx create_record lets us assert what record phi tried to
    write without actually hitting any PDS.
    """
    if not settings.anthropic_api_key:
        pytest.skip("Requires ANTHROPIC_API_KEY")

    if settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

    personality = Path(settings.personality_file).read_text()
    skills_dir = Path(__file__).parent.parent / "skills"

    agent = Agent[None, Response](
        name="phi-skills-test",
        model="anthropic:claude-haiku-4-5-20251001",
        system_prompt=personality,
        output_type=Response,
        toolsets=[SkillsToolset(directories=[str(skills_dir)])],
    )

    @agent.tool
    async def mcp__pdsx__create_record(
        ctx: RunContext[None],
        collection: str,
        record: dict,
        rkey: str | None = None,
    ) -> str:
        """Create a new atproto record on phi's PDS via pdsx MCP.

        collection: the lexicon NSID (e.g. 'network.cosmik.card')
        record: the record body matching the lexicon's schema
        rkey: optional record key (auto-generated if omitted)
        """
        _spy.record(
            "mcp__pdsx__create_record",
            collection=collection,
            record=record,
            rkey=rkey,
        )
        return f'{{"uri": "at://did:plc:test/{collection}/3xxxxx", "cid": "bafytest"}}'

    @agent.tool
    async def semble_search(ctx: RunContext[None], query: str) -> str:
        """Find semble api methods by keyword. Returns method names you can
        inspect with semble_get_schema and call inside semble_execute."""
        _spy.record("semble_search", query=query)
        return (
            "cards_add_url, cards_get_library_status, cards_list_mine, "
            "search_semantic, connections_create, collections_create, "
            "actors_get_my_profile"
        )

    @agent.tool
    async def semble_get_schema(ctx: RunContext[None], tools: list[str]) -> str:
        """Get parameter schemas for semble api methods by name."""
        _spy.record("semble_get_schema", tools=tools)
        schemas = {
            "cards_add_url": (
                "cards_add_url(url: str, *, note: str | None = None, "
                "collection_ids: list[str] | None = None) -> "
                '{"urlCardId": str, "noteCardId": str | None}'
            ),
            "cards_get_library_status": (
                'cards_get_library_status(url: str) -> {"inLibrary": bool}'
            ),
        }
        return "\n".join(schemas.get(t, f"{t}: (schema available)") for t in tools)

    @agent.tool
    async def semble_execute(ctx: RunContext[None], code: str) -> str:
        """Run python code composing semble api methods in a sandbox.
        Call methods with `await call_tool("method_name", {...})`; the last
        expression or `return` value is the result. Use for reads and writes
        against your public knowledge graph (semble/cosmik)."""
        _spy.record("semble_execute", code=code)
        return '{"urlCardId": "11111111-2222-3333-4444-555555555555", "noteCardId": "66666666-7777-8888-9999-000000000000"}'

    class SkillsTestAgent:
        def __init__(self):
            self.agent = agent
            self.spy = _spy

        async def process_request(self, text: str) -> Response:
            result = await self.agent.run(text)
            self.last_messages = result.all_messages()
            return result.output

        def loaded_skills(self) -> list[str]:
            """Walk the message history for load_skill tool calls."""
            loaded: list[str] = []
            for msg in self.last_messages:
                for part in getattr(msg, "parts", []):
                    if (
                        getattr(part, "part_kind", None) == "tool-call"
                        and getattr(part, "tool_name", None) == "load_skill"
                    ):
                        args = getattr(part, "args", {})
                        if isinstance(args, dict):
                            name = args.get("skill_name") or args.get("name")
                            if name:
                                loaded.append(name)
            return loaded

    return SkillsTestAgent()


@pytest.fixture(autouse=True)
def _reset_spy():
    _spy.reset()


async def test_loads_cosmik_skill_when_saving_a_url(skills_agent):
    """Asked to bookmark a URL, phi should load cosmik-records before writing."""
    await skills_agent.process_request(
        "save this URL to your public memory: "
        "https://transformer-circuits.pub/2026/emotions/ — anthropic's emotion "
        "interpretability paper. include a brief description of why you're "
        "saving it."
    )

    loaded = skills_agent.loaded_skills()
    assert "cosmik-records" in loaded, (
        f"expected cosmik-records skill to be loaded; loaded={loaded}"
    )


async def test_saves_url_via_semble_execute(skills_agent):
    """Phi should save a URL through semble_execute (cards_add_url), not raw pdsx."""
    await skills_agent.process_request(
        "save this URL to your public memory: "
        "https://transformer-circuits.pub/2026/emotions/ — anthropic's emotion "
        "interpretability paper. include a brief description of why you're "
        "saving it."
    )

    spy = skills_agent.spy
    assert spy.was_called("semble_execute"), "semble_execute was not called"
    code = "\n".join(c["code"] for c in spy.calls["semble_execute"])
    assert "cards_add_url" in code, f"cards_add_url not in executed code: {code}"
    assert "transformer-circuits.pub" in code, f"URL not in executed code: {code}"
    assert not spy.was_called("mcp__pdsx__create_record"), (
        "URL save should route through semble, not raw pdsx create_record: "
        f"{spy.calls['mcp__pdsx__create_record']}"
    )


async def test_standalone_note_routes_to_pdsx(skills_agent):
    """Text-only public notes are the one cosmik write still on pdsx."""
    await skills_agent.process_request(
        "write a standalone public note to your knowledge graph (not a bsky "
        "post): the observation that typed sdk surfaces beat hand-rolled "
        "http parsing because validation errors arrive before the network."
    )

    spy = skills_agent.spy
    assert spy.was_called("mcp__pdsx__create_record"), "create_record was not called"
    call = spy.calls["mcp__pdsx__create_record"][0]
    assert call["collection"] == "network.cosmik.card", (
        f"wrong collection: {call['collection']}"
    )
    assert call["record"].get("kind") == "NOTE", (
        f"expected kind=NOTE, got: {call['record']}"
    )
