"""Test that proves tools are actually being used by the agent"""

import os
from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelHTTPError

from bot.config import settings


class Response(BaseModel):
    text: str = Field(description="Response text")


class TestToolUsage:
    def setup_method(self):
        """Set up API key for tests"""
        if settings.anthropic_api_key:
            os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

    async def test_agent_uses_tools(self):
        """Test that the agent actually calls tools when appropriate"""

        if not settings.anthropic_api_key:
            pytest.skip("No Anthropic API key configured")

        # Track tool calls
        tool_calls: list[str] = []

        # Create agent
        agent = Agent(
            "anthropic:claude-haiku-4-5",
            system_prompt="You are a helpful assistant. Use tools when asked.",
            output_type=Response,
        )

        # Register a simple tool
        @agent.tool
        async def get_current_time(ctx: RunContext[None]) -> str:
            """Get the current time"""
            tool_calls.append("get_current_time")
            return "The current time is 3:14 PM"

        # Test 1: Query that should NOT use the tool
        result = await agent.run("What is 2 + 2?")
        assert len(tool_calls) == 0, "Tool was called for simple math question"

        # Test 2: Query that SHOULD use the tool
        result = await agent.run("What time is it?")
        assert len(tool_calls) == 1, (
            f"Tool was not called for time question. Calls: {tool_calls}"
        )
        assert tool_calls[0] == "get_current_time"
        assert "3:14" in result.output.text, (
            f"Tool result not in response: {result.output.text}"
        )

    async def test_search_tool_usage(self):
        """Test that search tool is called for appropriate queries"""

        if not settings.anthropic_api_key:
            pytest.skip("No Anthropic API key configured")

        tool_calls: list[dict] = []

        agent = Agent(
            "anthropic:claude-haiku-4-5",
            system_prompt="You help answer questions. Use search for current events.",
            output_type=Response,
        )

        @agent.tool
        async def search_web(ctx: RunContext[None], query: str) -> str:
            """Search the web for information"""
            tool_calls.append({"tool": "search_web", "query": query})
            return f"Search results for '{query}': Latest news about {query}"

        try:
            # Should NOT search for simple math
            result = await agent.run("What is 2 + 2?")
            assert len(tool_calls) == 0, f"Searched for basic math. Calls: {tool_calls}"

            # SHOULD search for current events
            result = await agent.run("What happened in tech news today?")
            assert len(tool_calls) > 0, (
                f"Did not search for current news. Response: {result.output.text}"
            )
            assert tool_calls[0]["tool"] == "search_web"
            assert (
                "tech" in tool_calls[0]["query"].lower()
                or "news" in tool_calls[0]["query"].lower()
            )
        except ModelHTTPError:
            pytest.skip("Anthropic API unavailable")

    async def test_multiple_tool_calls(self):
        """Test that agent can call tools multiple times in one request"""

        if not settings.anthropic_api_key:
            pytest.skip("No Anthropic API key configured")

        calls: list[str] = []

        agent = Agent(
            "anthropic:claude-haiku-4-5",
            system_prompt="You are a helpful assistant.",
            output_type=Response,
        )

        @agent.tool
        async def search_web(ctx: RunContext[None], query: str) -> str:
            """Search for information"""
            calls.append(f"search: {query}")
            return f"Info about {query}"

        # Ask for multiple things that need searching
        await agent.run("Search for information about Python and also about Rust")

        assert len(calls) >= 2, f"Expected multiple searches, got {len(calls)}: {calls}"
        assert any("Python" in call for call in calls), f"No Python search in: {calls}"
        assert any("Rust" in call for call in calls), f"No Rust search in: {calls}"


class TestPhiAgentToolRegistration:
    """Verify that PhiAgent registers all expected tools (no LLM calls needed)."""

    def setup_method(self):
        if settings.anthropic_api_key:
            os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

    def test_graze_tools_registered(self):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("No Anthropic API key configured")

        with patch("bot.core.atproto_client.bot_client"):
            from bot.agent import PhiAgent

            agent = PhiAgent()
            tool_names = {t.name for t in agent.agent._function_toolset.tools.values()}
            assert "create_feed" in tool_names, f"create_feed not in {tool_names}"
            assert "list_feeds" in tool_names, f"list_feeds not in {tool_names}"
            assert "read_timeline" in tool_names, f"read_timeline not in {tool_names}"
            assert "read_feed" in tool_names, f"read_feed not in {tool_names}"
            assert "follow_user" in tool_names, f"follow_user not in {tool_names}"
            assert "get_own_posts" in tool_names, f"get_own_posts not in {tool_names}"

    def test_graze_client_instantiated(self):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("No Anthropic API key configured")

        with patch("bot.core.atproto_client.bot_client"):
            from bot.agent import PhiAgent

            agent = PhiAgent()
            assert agent.graze_client is not None
            assert agent.graze_client._handle == settings.bluesky_handle
