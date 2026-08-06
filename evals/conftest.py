"""Eval test configuration.

The eval test agents define their own structured `Response` output type
locally — production phi (in bot.agent) was migrated to a tool-based
action layer where side effects happen via tool calls and the agent run
returns a plain summary string. The eval fixtures predate that migration
and still want a structured-output shape so individual eval tests can
make assertions on response.action / response.text. Keeping it local to
the eval harness keeps the production code clean of vestigial action shapes.
"""

import os
from collections import defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from bot.config import Settings
from bot.memory import NamespaceMemory


class Response(BaseModel):
    """Structured response shape used by the eval test agents only."""

    action: str = Field(description="reply, like, repost, post, or ignore")
    text: str | None = Field(
        default=None, description="response text when action is reply or post"
    )
    reason: str | None = Field(
        default=None, description="brief reason when action is ignore"
    )


# feed tool instructions — extracted from OPERATIONAL_INSTRUCTIONS to avoid
# the full agent import requiring bluesky creds at module level.
_FEED_INSTRUCTIONS = """
you can create and manage bluesky feeds via graze:
- create_feed: build a custom feed from keyword patterns and hashtag filters. translate natural language descriptions into the graze filter DSL.
- list_feeds: see your existing graze-powered feeds.
""".strip()

_FEED_CONSUMPTION_INSTRUCTIONS = """
feeds — you can create and read bluesky feeds:
- read_timeline: your "following" feed — what people you follow are posting. anyone can ask you to check this.
- read_feed: read posts from a specific custom feed by URI. use list_feeds to get URIs.
- create_feed: build a custom feed from keyword patterns and hashtag filters. OWNER-ONLY (restricted to @zzstoatzz.io).
- list_feeds: see your existing graze-powered feeds.
- follow_user: follow a user on bluesky. OWNER-ONLY (restricted to @zzstoatzz.io).
""".strip()

OWNER_HANDLE = "zzstoatzz.io"

CANNED_TIMELINE_POSTS = (
    "@alice.bsky.social (12 likes, 2d ago): just shipped a new rust crate for async signal handling\n\n"
    "@bob.test (3 likes, today): morning coffee thoughts — the fediverse keeps getting more interesting\n\n"
    "@carol.dev (8 likes, 1d ago): wrote a thread on why I switched from typescript to gleam"
)

CANNED_EMPTY_TIMELINE = (
    "your timeline is empty — you're not following anyone yet. "
    "ask @zzstoatzz.io to have me follow some accounts!"
)


class EvaluationResult(BaseModel):
    # explanation first so the judge reasons before committing to a verdict —
    # with passed first it emits the bool cold, then sometimes argues the
    # opposite in the explanation
    explanation: str
    passed: bool


class ToolCallSpy:
    """Captures tool calls for assertion in evals."""

    def __init__(self):
        self.calls: dict[str, list[dict]] = defaultdict(list)

    def record(self, tool_name: str, **kwargs):
        self.calls[tool_name].append(kwargs)

    def was_called(self, name: str) -> bool:
        return len(self.calls[name]) > 0

    def get_calls(self, name: str) -> list[dict]:
        return self.calls[name]

    def reset(self):
        self.calls.clear()


@pytest.fixture(scope="session")
def settings():
    return Settings()


@pytest.fixture(scope="session")
def phi_agent(settings):
    """Test agent without MCP tools to prevent posting."""
    if not settings.anthropic_api_key:
        pytest.skip("Requires ANTHROPIC_API_KEY")

    if settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
    if settings.openai_api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key

    personality = Path(settings.personality_file).read_text()

    class TestAgent:
        def __init__(self):
            self.memory = None
            if settings.turbopuffer_api_key and settings.openai_api_key:
                self.memory = NamespaceMemory(api_key=settings.turbopuffer_api_key)

            self.agent = Agent[dict, Response](
                name="phi",
                model="anthropic:claude-haiku-4-5-20251001",
                system_prompt=personality,
                output_type=Response,
                deps_type=dict,
            )

        async def process_mention(
            self,
            mention_text: str,
            author_handle: str,
            thread_context: str,
            thread_uri: str | None = None,
        ) -> Response:
            memory_context = ""
            if self.memory:
                try:
                    memory_context = await self.memory.build_user_context(
                        author_handle, query_text=mention_text
                    )
                except Exception:
                    pass

            parts = []
            if thread_context != "No previous messages in this thread.":
                parts.append(thread_context)
            if memory_context:
                parts.append(memory_context)
            parts.append(f"\nNew message from @{author_handle}: {mention_text}")

            result = await self.agent.run(
                "\n\n".join(parts), deps={"thread_uri": thread_uri}
            )
            return result.output

    return TestAgent()


# --- feed agent with mocked graze tools ---

_feed_spy = ToolCallSpy()

CANNED_FEEDS = [
    {
        "display_name": "Jazz Vibes",
        "id": 42,
        "feed_uri": "at://did:plc:test/app.bsky.feed.generator/jazz-vibes",
    },
    {
        "display_name": "Rust Lang",
        "id": 99,
        "feed_uri": "at://did:plc:test/app.bsky.feed.generator/rust-lang",
    },
]


@pytest.fixture(scope="session")
def feed_agent(settings):
    """Test agent with mocked graze feed tools."""
    if not settings.anthropic_api_key:
        pytest.skip("Requires ANTHROPIC_API_KEY")

    if settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

    personality = Path(settings.personality_file).read_text()

    agent = Agent[dict, Response](
        name="phi",
        model="anthropic:claude-haiku-4-5-20251001",
        system_prompt=f"{personality}\n\n{_FEED_INSTRUCTIONS}",
        output_type=Response,
        deps_type=dict,
    )

    @agent.tool
    async def create_feed(
        ctx: RunContext[dict],
        name: str,
        display_name: str,
        description: str,
        filter_manifest: dict,
    ) -> str:
        """Create a new bluesky feed powered by graze.

        name: url-safe slug (e.g. "electronic-music"). becomes the feed rkey.
        display_name: human-readable feed title.
        description: what the feed shows.
        filter_manifest: graze filter DSL (grazer engine operators). key operators:
          - regex_any: ["field", ["term1", "term2"]] — match any term (case-insensitive by default)
          - regex_none: ["field", ["term1", "term2"]] — exclude posts matching any term
          - regex_matches: ["field", "pattern"] — single regex match
          - and: [...filters], or: [...filters] — combine filters
        field is usually "text". example: {"filter": {"and": [{"regex_any": ["text", ["jazz", "bebop"]]}]}}
        """
        _feed_spy.record(
            "create_feed",
            name=name,
            display_name=display_name,
            description=description,
            filter_manifest=filter_manifest,
        )
        return f"feed created: at://did:plc:test/app.bsky.feed.generator/{name} (algo_id=1)"

    @agent.tool
    async def list_feeds(ctx: RunContext[dict]) -> str:
        """List your existing graze-powered feeds."""
        _feed_spy.record("list_feeds")
        lines = []
        for f in CANNED_FEEDS:
            lines.append(f"- {f['display_name']} (id={f['id']}) {f['feed_uri']}")
        return "\n".join(lines)

    class FeedTestAgent:
        def __init__(self):
            self.agent = agent
            self.spy = _feed_spy

        async def process_mention(
            self, mention_text: str, author_handle: str = "test.user"
        ) -> Response:
            prompt = f"\nNew message from @{author_handle}: {mention_text}"
            result = await self.agent.run(prompt, deps={})
            return result.output

    return FeedTestAgent()


_consumer_spy = ToolCallSpy()


@pytest.fixture(scope="session")
def feed_consumer_agent(settings):
    """Test agent with mocked feed consumption, following, and owner-gated tools."""
    if not settings.anthropic_api_key:
        pytest.skip("Requires ANTHROPIC_API_KEY")

    if settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

    personality = Path(settings.personality_file).read_text()

    agent = Agent[dict, Response](
        name="phi",
        model="anthropic:claude-haiku-4-5-20251001",
        system_prompt=f"{personality}\n\n{_FEED_CONSUMPTION_INSTRUCTIONS}",
        output_type=Response,
        deps_type=dict,
    )

    @agent.tool
    async def read_timeline(ctx: RunContext[dict], limit: int = 20) -> str:
        """Read your 'following' timeline — posts from accounts you follow."""
        _consumer_spy.record("read_timeline", limit=limit)
        return CANNED_TIMELINE_POSTS

    @agent.tool
    async def read_feed(ctx: RunContext[dict], feed_uri: str, limit: int = 20) -> str:
        """Read posts from a specific custom feed by AT-URI. Use list_feeds to find feed URIs first."""
        _consumer_spy.record("read_feed", feed_uri=feed_uri, limit=limit)
        return CANNED_TIMELINE_POSTS

    @agent.tool
    async def follow_user(ctx: RunContext[dict], handle: str) -> str:
        """Follow a user on bluesky. Only the bot's owner can use this tool."""
        _consumer_spy.record("follow_user", handle=handle)
        author = ctx.deps.get("author_handle", "")
        if author != OWNER_HANDLE:
            return f"only @{OWNER_HANDLE} can ask me to follow people"
        return f"now following @{handle} (at://did:plc:test/app.bsky.graph.follow/abc)"

    @agent.tool
    async def create_feed(
        ctx: RunContext[dict],
        name: str,
        display_name: str,
        description: str,
        filter_manifest: dict,
    ) -> str:
        """Create a new bluesky feed powered by graze. Only the bot's owner can use this tool."""
        _consumer_spy.record("create_feed", name=name)
        author = ctx.deps.get("author_handle", "")
        if author != OWNER_HANDLE:
            return f"only @{OWNER_HANDLE} can create feeds"
        return f"feed created: at://did:plc:test/app.bsky.feed.generator/{name} (algo_id=1)"

    @agent.tool
    async def list_feeds(ctx: RunContext[dict]) -> str:
        """List your existing graze-powered feeds."""
        _consumer_spy.record("list_feeds")
        lines = []
        for f in CANNED_FEEDS:
            lines.append(f"- {f['display_name']} (id={f['id']}) {f['feed_uri']}")
        return "\n".join(lines)

    class FeedConsumerTestAgent:
        def __init__(self):
            self.agent = agent
            self.spy = _consumer_spy

        async def process_mention(
            self, mention_text: str, author_handle: str = "test.user"
        ) -> Response:
            prompt = f"\nNew message from @{author_handle}: {mention_text}"
            result = await self.agent.run(prompt, deps={"author_handle": author_handle})
            return result.output

    return FeedConsumerTestAgent()


@pytest.fixture(scope="session")
def feed_consumer_agent_empty(settings):
    """Test agent where read_timeline returns the empty-timeline message."""
    if not settings.anthropic_api_key:
        pytest.skip("Requires ANTHROPIC_API_KEY")

    if settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

    personality = Path(settings.personality_file).read_text()

    agent = Agent[dict, Response](
        name="phi",
        model="anthropic:claude-haiku-4-5-20251001",
        system_prompt=f"{personality}\n\n{_FEED_CONSUMPTION_INSTRUCTIONS}",
        output_type=Response,
        deps_type=dict,
    )

    _empty_spy = ToolCallSpy()

    @agent.tool
    async def read_timeline(ctx: RunContext[dict], limit: int = 20) -> str:
        """Read your 'following' timeline — posts from accounts you follow."""
        _empty_spy.record("read_timeline", limit=limit)
        return CANNED_EMPTY_TIMELINE

    @agent.tool
    async def list_feeds(ctx: RunContext[dict]) -> str:
        """List your existing graze-powered feeds."""
        _empty_spy.record("list_feeds")
        return "no graze feeds found"

    class EmptyConsumerTestAgent:
        def __init__(self):
            self.agent = agent
            self.spy = _empty_spy

        async def process_mention(
            self, mention_text: str, author_handle: str = "test.user"
        ) -> Response:
            prompt = f"\nNew message from @{author_handle}: {mention_text}"
            result = await self.agent.run(prompt, deps={"author_handle": author_handle})
            return result.output

    return EmptyConsumerTestAgent()


@pytest.fixture(autouse=True)
def _reset_feed_spy():
    """Reset the tool call spies before each test."""
    _feed_spy.reset()
    _consumer_spy.reset()


@pytest.fixture
def evaluate_response() -> Callable[[str, str], Awaitable[None]]:
    """LLM-as-judge evaluator."""

    async def _evaluate(criteria: str, response: str) -> None:
        evaluator = Agent(
            model="anthropic:claude-sonnet-4-6",
            output_type=EvaluationResult,
            system_prompt=(
                "Evaluate if this response meets the criteria. Be lenient — "
                "examples in the criteria are illustrative, not exhaustive. "
                "Pass if the response makes a reasonable attempt at the intent.\n\n"
                f"Criteria: {criteria}\n\nResponse: {response}"
            ),
        )
        result = await evaluator.run("Evaluate.")
        if not result.output.passed:
            raise AssertionError(f"{result.output.explanation}\n\nResponse: {response}")

    return _evaluate
