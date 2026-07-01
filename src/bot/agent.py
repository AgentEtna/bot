"""MCP-enabled agent for phi with structured memory."""

import contextlib
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_ai import Agent, ImageUrl, RunContext
from pydantic_ai.mcp import MCPServerStreamableHTTP
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai_skills import SkillsToolset
from semble import AsyncSemble

from bot.config import settings
from bot.core.atlas import get_atlas_digest
from bot.core.atproto_client import bot_client, get_identity_block
from bot.core.discovery_pool import get_discovery_pool_block
from bot.core.docket import get_docket_digest
from bot.core.goals import list_goals as list_goal_records
from bot.core.graze_client import GrazeClient
from bot.core.operator import get_operator_profile
from bot.core.owned_feeds import get_owned_feeds_block
from bot.core.recent_flow_mentions import get_recent_flow_mentions_block
from bot.core.recent_operations import get_operations_block
from bot.core.self_state import get_state_block
from bot.core.workflow_state import get_workflow_state_block
from bot.memory.extraction import EXTRACTION_SYSTEM_PROMPT, ExtractionResult
from bot.memory.namespace_memory import InteractionRow
from bot.status import bot_status
from bot.tools import PhiDeps, _check_services_impl, register_all
from bot.tools.bluesky import fetch_relay_names
from bot.utils.time import humanize_duration

logger = logging.getLogger("bot.agent")


def _build_operational_instructions() -> str:
    """Cross-cutting rules that don't fit in any single tool's docstring.

    Each tool's per-tool guidance lives in its own docstring (the framework
    surfaces those to the model). This function is for rules that span tools
    or that no docstring can naturally express.
    """
    from bot.core.policy import POLICIES

    policies_block = "\n".join(f"- {slug}: {text}" for slug, text in POLICIES.items())
    return f"""
posting flows through post / like_post / repost_post — raw atproto record-create tools (pdsx) bypass the consent layer. top-level posts and replies are the same call: `post(text)` for a top-level, `post(text, in_reply_to=uri)` for a reply (including threading your own posts).

your policies — these are yours to hold, and an independent policy check also reviews every `post` call (with its provenance: invited vs unprompted) before it executes:
{policies_block}

a blocked post returns the policy and reason as your tool result; nothing was posted. treat it as information, not punishment — adapt (a like, save_memory, a different post) rather than retrying verbatim. a policy note on a successful post means you're drifting toward a boundary; let it register.

your public knowledge graph (cosmik/semble) flows through the semble tools: semble_search to find api methods, semble_get_schema for parameter shapes, semble_execute to compose reads and writes in one block. writes there land on your own PDS, attributed to you, scoped to network.cosmik.* — no owner gate, but they're public. the one exception is standalone NOTE cards (pdsx; the cosmik-records skill has the routing).

memory blocks carry their own trust labels. when a user's current words contradict stored notes, trust the words.

mention-consent allowlist: @{settings.owner_handle}, yourself, conversation participants, opted-in handles. mentions of anyone else render as plain text.

owner-like-as-approval cuts across every owner-gated tool: post the authorization request, the operator's like in the next batch authorizes the specific action discussed in that thread only — never a stranger's request riding the same batch.

target URIs for in_reply_to / like_post / repost_post are verified by fetch — a hallucinated URI refuses cleanly. pass URIs verbatim (from your notifications, recent operations, get_own_posts, or search_posts); never construct one from prose text.

when an atproto record may contain an image or text blob, use `inspect_record_media(uri=...)` to actually inspect allowed media. do not infer image details from link previews, alt text, card titles, or URLs when the record's pixels matter.
""".strip()


def _format_notifications_block(notifications_context: dict) -> str:
    """Format the notifications batch as a readable [NEW NOTIFICATIONS] block.

    Groups thread-style notifications (mention/reply/quote) by thread root so
    multiple posts in one conversation render as one section. Engagement items
    (like/repost/follow) are listed separately at the end. Each item shows its
    URI in brackets so the agent can pass it to the trusted posting tools.

    Cited posts (reason="cited") are rendered nested under the notification
    that referenced them, so phi sees them as structured, addressable refs —
    not just URLs inside prose. post(in_reply_to=...) accepts these URIs.
    """
    if not notifications_context:
        return ""

    # Group cited entries by their cited_by source so we can render them
    # nested under the notification that referenced them.
    cited_by_source: dict[str, list[dict]] = {}
    threads: dict[str, list[dict]] = {}
    engagement: list[dict] = []
    for entry in notifications_context.values():
        reason = entry.get("reason", "")
        if reason == "cited":
            src = entry.get("cited_by", "")
            cited_by_source.setdefault(src, []).append(entry)
        elif reason in ("mention", "reply", "quote"):
            root = entry.get("root_uri") or entry.get("uri", "")
            threads.setdefault(root, []).append(entry)
        else:
            engagement.append(entry)

    def _format_cited(e: dict) -> str:
        c_handle = e.get("author_handle", "?")
        c_uri = e.get("uri", "")
        c_text = (e.get("post_text", "") or "").replace("\n", " ")
        return f'  cited: @{c_handle} [{c_uri}]: "{c_text[:200]}"'

    lines: list[str] = []
    lines.append("[NEW NOTIFICATIONS]")

    for root_uri, entries in threads.items():
        entries.sort(key=lambda e: e.get("indexed_at", ""))
        thread_ctx = entries[0].get("thread_context", "") or ""

        lines.append("")
        if thread_ctx and thread_ctx != "No previous messages in this thread.":
            lines.append(thread_ctx)
            lines.append("")
        for e in entries:
            handle = e.get("author_handle", "?")
            uri = e.get("uri", "")
            text = e.get("post_text", "")
            embed = e.get("embed_desc") or ""
            embed_part = f"\n  {embed}" if embed else ""
            lines.append(f"@{handle} [{uri}]: {text}{embed_part}")
            for cited in cited_by_source.get(uri, []):
                lines.append(_format_cited(cited))

    if engagement:
        lines.append("")
        for e in engagement:
            handle = e.get("author_handle", "?")
            reason = e.get("reason", "")
            uri = e.get("uri", "")
            target_text = e.get("post_text", "")
            target_part = f' — "{target_text[:120]}"' if target_text else ""
            thread_ctx = e.get("thread_context") or ""
            if reason == "follow":
                lines.append(f"@{handle} followed you")
            else:
                lines.append(f"@{handle} {reason}d your post [{uri}]{target_part}")
                if thread_ctx and thread_ctx != "No previous messages in this thread.":
                    lines.append(f"  thread context:\n  {thread_ctx}")
                for cited in cited_by_source.get(uri, []):
                    lines.append(_format_cited(cited))

    return "\n".join(lines)


class PhiAgent:
    """phi - bluesky bot with structured memory and MCP tools."""

    def __init__(self):
        # Ensure API keys from settings are in environment for libraries that check os.environ
        if settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
        if settings.openai_api_key and not os.environ.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = settings.openai_api_key

        # Load personality
        personality_path = Path(settings.personality_file)
        self.base_personality = personality_path.read_text()

        # Initialize memory (TurboPuffer)
        if settings.turbopuffer_api_key and settings.openai_api_key:
            from bot.memory import NamespaceMemory

            self.memory = NamespaceMemory(api_key=settings.turbopuffer_api_key)
            logger.info("memory enabled (turbopuffer)")
        else:
            self.memory = None
            logger.warning("no memory - missing turbopuffer or openai key")

        # Skills — filesystem-backed, progressive disclosure. The preamble
        # (skill names + descriptions) is injected automatically by the
        # toolset on pydantic-ai>=1.74. Full SKILL.md bodies are loaded on
        # demand via load_skill.
        #
        # exclude_tools=['run_skill_script']: every skill we ship is
        # documentation-only (markdown bodies + resource files). leaving
        # the script-execution tool registered is extra capability surface
        # phi never uses — and would silently expose subprocess execution
        # if someone added a script to a skill folder by accident.
        self.skills_toolset = SkillsToolset(
            directories=[settings.skills_dir],
            exclude_tools=["run_skill_script"],
        )
        self.graze_client = GrazeClient(
            handle=settings.bluesky_handle, password=settings.bluesky_password
        )

        # Create PydanticAI agent without MCP toolsets — they're created
        # fresh per agent.run() call to avoid the cancel scope bug:
        # https://github.com/pydantic/pydantic-ai/issues/2818
        #
        # output_type=str: the agent's "decision" is no longer a structured
        # action — actions happen as tool calls during the run (post,
        # like_post, etc). The final string return is just a brief summary
        # for logging.
        # anthropic prompt caching — tool definitions are perfectly static
        # across runs (~30 tools; observed ~12k tokens cached). 1h TTL chosen
        # for active-period coverage: tool-call loops, notification bursts,
        # startup ritual, and any clustered traffic. it does NOT bridge the
        # 4-hour cycle cadence; between cycles the cache will normally lapse.
        # break-even on the write premium is ~1-2 reads: 1h writes cost +100%
        # of base input, hits cost 10%, so each hit saves 90% of base while
        # the write costs 100% extra over base — recouped after the second
        # read on cached prefix.
        #
        # instructions/messages caching is deliberately NOT enabled yet — the
        # dynamic system_prompt callbacks below (including [NEW NOTIFICATIONS],
        # episodic recall, per-author memory) would invalidate any system-
        # prefix cache every run. the follow-up refactor introduces a runtime
        # context builder that appends event state to the user message under
        # a [SYSTEM NOTIFICATION] header, leaving the system prompt stable.
        self.agent = Agent[PhiDeps, str](
            name="phi",
            model=settings.agent_model,
            system_prompt=(
                "the following is your personality: "
                f"{self.base_personality}\n\n"
                "--- operational rules below (these are constraints) ---\n\n"
                f"{_build_operational_instructions()}"
            ),
            model_settings=AnthropicModelSettings(
                anthropic_cache_tool_definitions="1h",
            ),
            output_type=str,
            deps_type=PhiDeps,
            toolsets=[self.skills_toolset],
        )

        # --- dynamic system prompts ---

        @self.agent.system_prompt(dynamic=True)
        async def inject_identity() -> str:
            return await get_identity_block()

        @self.agent.system_prompt(dynamic=True)
        async def inject_operator() -> str:
            """[OPERATOR] — resolved profile of the bot's owner."""
            profile = await get_operator_profile()
            if not profile:
                return ""
            name = profile["display_name"]
            handle = profile["handle"]
            did = profile["did"]
            return f"[OPERATOR]: {name} (@{handle}, {did})"

        @self.agent.system_prompt(dynamic=True)
        def inject_today() -> str:
            """[NOW] anchored to both UTC and the operator's local clock.

            phi runs on the operator's clock — schedule slots (musings,
            reflection) fire at operator-local hours so posts land at human
            times of day for the person reading them. surfacing both lines
            here means phi can reason about "is it morning where you are"
            without having to convert in her head.
            """
            now_utc = datetime.now(UTC)
            try:
                tz = ZoneInfo(settings.operator_timezone)
                now_local = now_utc.astimezone(tz)
                local_line = (
                    f"[NOW (operator local)]: "
                    f"{now_local.strftime('%Y-%m-%d %H:%M %Z')} "
                    f"({settings.operator_timezone}) — "
                    f"this is the operator's clock; your scheduled posting "
                    f"slots are anchored to it so things land at human times "
                    f"of day for them."
                )
            except ZoneInfoNotFoundError:
                local_line = ""
            utc_line = f"[NOW]: {now_utc.strftime('%Y-%m-%d %H:%M UTC')}"
            return f"{utc_line}\n{local_line}" if local_line else utc_line

        @self.agent.system_prompt(dynamic=True)
        def inject_pause_history() -> str:
            """[OPERATIONAL HISTORY] — most recent pause cycle.

            Renders whenever a complete pause/resume cycle exists and the
            resume was within the last 24h. Duration isn't filtered — phi
            sees whatever happened and decides what (if anything) it means
            for this batch.
            """
            paused_at = bot_status.paused_at
            resumed_at = bot_status.resumed_at
            if not paused_at or not resumed_at:
                return ""
            if resumed_at <= paused_at:
                return ""  # currently paused, or never resumed since this pause
            since_resume = datetime.now(UTC) - resumed_at
            if since_resume > timedelta(hours=24):
                return ""  # ancient history; the catchup is over
            offline = resumed_at - paused_at
            return (
                "[OPERATIONAL HISTORY]: paused "
                f"{paused_at.strftime('%Y-%m-%d %H:%M UTC')}, resumed "
                f"{resumed_at.strftime('%Y-%m-%d %H:%M UTC')} "
                f"(offline {humanize_duration(offline)})."
            )

        @self.agent.system_prompt(dynamic=True)
        async def inject_known_relays() -> str:
            """List the valid relay hostnames for check_relays(name=...)."""
            names = await fetch_relay_names()
            if not names:
                return ""
            return "[KNOWN RELAYS]: " + ", ".join(names)

        @self.agent.system_prompt(dynamic=True)
        async def inject_self_state() -> str:
            """How phi looks from outside + canonical pointers (last follow, queue)."""
            return await get_state_block(bot_client, self.memory)

        @self.agent.system_prompt(dynamic=True)
        async def inject_recent_operations() -> str:
            """[RECENT OPERATIONS] — last N PDS writes across collections, for continuity."""
            return await get_operations_block(bot_client)

        @self.agent.system_prompt(dynamic=True)
        async def inject_discovery_pool(ctx: RunContext[PhiDeps]) -> str:
            """[DISCOVERY POOL] — strangers the operator has been liking; warm leads."""
            return await get_discovery_pool_block(ctx.deps.memory)

        @self.agent.system_prompt(dynamic=True)
        def inject_notifications(ctx: RunContext[PhiDeps]) -> str:
            """Render the notifications batch as the [NEW NOTIFICATIONS] block."""
            return _format_notifications_block(ctx.deps.notifications_context or {})

        @self.agent.system_prompt(dynamic=True)
        async def inject_user_memory(ctx: RunContext[PhiDeps]) -> str:
            """Inject per-author memory blocks for every unique author in the batch.

            For each unique author across the notifications context, build a
            memory block keyed on the union of their post texts in this batch
            (so semantic search returns memories relevant to what they're
            currently saying). Core memory is fetched once via the first block
            to avoid repetition.
            """
            if not ctx.deps.memory:
                return ""
            notifs = ctx.deps.notifications_context or {}
            if not notifs:
                return ""

            by_author: dict[str, list[str]] = {}
            for entry in notifs.values():
                handle = entry.get("author_handle")
                text = entry.get("post_text", "")
                if handle and handle not in (
                    settings.owner_handle,
                    settings.bluesky_handle,
                ):
                    by_author.setdefault(handle, []).append(text or "")

            if not by_author:
                return ""

            blocks: list[str] = []
            for handle, texts in by_author.items():
                query = " ".join(t for t in texts if t) or handle
                try:
                    block = await ctx.deps.memory.build_user_context(
                        handle, query_text=query
                    )
                    if block:
                        blocks.append(block)
                except Exception as e:
                    logger.warning(f"failed to retrieve memories for @{handle}: {e}")
            return "\n\n".join(blocks)

        @self.agent.system_prompt(dynamic=True)
        async def inject_episodic(ctx: RunContext[PhiDeps]) -> str:
            if not ctx.deps.memory:
                return ""
            # Batch notifications have a real semantic seed: the posts phi is
            # reacting to. Scheduled paths have task text like "you have a
            # moment", which made vector recall noisy; let those paths call
            # search_memory explicitly when they need private memory.
            notifs = ctx.deps.notifications_context or {}
            if not notifs:
                return ""
            texts = [
                e.get("post_text", "") for e in notifs.values() if e.get("post_text")
            ]
            query = " ".join(texts)
            if not query:
                return ""
            # Pass phi's goals so the synthesis can rank by relevance to intent.
            try:
                goals = await list_goal_records(bot_client)
            except Exception:
                goals = []
            try:
                episodic_context = await ctx.deps.memory.get_episodic_context(
                    query, goals=goals
                )
                if episodic_context:
                    return episodic_context
            except Exception as e:
                logger.warning(f"failed to retrieve episodic memories: {e}")
            return ""

        @self.agent.system_prompt(dynamic=True)
        async def inject_atlas_digest() -> str:
            """[ATLAS] — daily distilled shape of phi's mind. Computed by the
            phi-atlas Prefect flow once a day; phi sees the digest here for
            free, and can drill into specific clusters / promotion candidates
            via the inspect_atlas tool.
            """
            try:
                return await get_atlas_digest()
            except Exception as e:
                logger.debug(f"atlas digest fetch failed: {e}")
                return ""

        @self.agent.system_prompt(dynamic=True)
        async def inject_docket_digest() -> str:
            """[DOCKET] — daily promotion candidates emitted by the docket
            Prefect flow after each atlas. Tiny block: title + suggested
            shape per candidate, nothing more. Full evidence + rationale is
            one pdsx.get_record away. The docket is an object phi can reach
            for, not another state block.
            """
            try:
                return await get_docket_digest()
            except Exception as e:
                logger.debug(f"docket digest fetch failed: {e}")
                return ""

        @self.agent.system_prompt(dynamic=True)
        async def inject_owned_feeds() -> str:
            """[OWNED FEEDS] — phi's curated graze feeds, surfaced by name."""
            try:
                return await get_owned_feeds_block(self.graze_client)
            except Exception as e:
                logger.debug(f"owned feeds inject failed: {e}")
                return ""

        @self.agent.system_prompt(dynamic=True)
        async def inject_public_memory() -> str:
            """One-line summary of phi's cosmik state.

            Just enough for phi to know it has public collections — the
            details are available through the semble tools when phi
            actually needs them. Counts come from the semble appview
            (uncapped, reflects what's actually indexed); the raw-PDS
            list_records path is the fallback when the appview is down.
            """
            await bot_client.authenticate()
            if not bot_client.client.me:
                return ""
            did = bot_client.client.me.did
            try:
                async with AsyncSemble() as semble:
                    profile = await semble.actors.get_profile(
                        identifier=did, include_stats=True
                    )
                cards = profile.url_card_count or 0
                cols = profile.collection_count or 0
                conns = profile.connection_count or 0
                if cards or cols or conns:
                    return (
                        f"[SEMBLE]: {cols} public collections, {cards} cards, "
                        f"{conns} connections."
                    )
            except Exception as e:
                logger.debug(f"semble appview profile fetch failed: {e}")
            try:
                cols = bot_client.client.com.atproto.repo.list_records(
                    {
                        "repo": did,
                        "collection": "network.cosmik.collection",
                        "limit": 50,
                    }
                )
                cards = bot_client.client.com.atproto.repo.list_records(
                    {
                        "repo": did,
                        "collection": "network.cosmik.card",
                        "limit": 50,
                    }
                )
                nc = len(cols.records) if cols.records else 0
                nk = len(cards.records) if cards.records else 0
                if nc or nk:
                    return f"[SEMBLE]: {nc} public collections, {nk} cards."
            except Exception as e:
                logger.debug(f"failed to fetch cosmik counts: {e}")
            return ""

        # --- register tools from tools/ package ---

        register_all(self.agent, self.graze_client)

        # Extraction agent — phi extracts its own observations using its own model
        self._extraction_agent = Agent[None, ExtractionResult](
            name="phi-extractor",
            model=settings.agent_model,
            system_prompt=f"{self.base_personality}\n\n{EXTRACTION_SYSTEM_PROMPT}",
            output_type=ExtractionResult,
        )

        logger.info(
            "phi agent initialized with pdsx, pub-search, and prefect MCP tools"
        )

    def get_capabilities(self) -> list[dict]:
        """Plain-data introspection of phi's registered function-tools.

        Reads from `self.agent._function_toolset.tools` (where pydantic-ai
        stores the registered `@agent.tool` callables). Returns one entry
        per tool with:
          - name: the registered tool name
          - description: the tool's docstring (what gets sent to the LLM)
          - operator_only: heuristic — true if the tool is gated to the
            bot's owner. Detected via either an `_is_owner(` source-call
            or owner-restriction phrasing in the docstring. When an
            explicit owner-gating attribute lands on `Tool`, swap this
            heuristic for a direct read.

        Surfaced via /api/abilities so the cockpit UI can render real
        names + real docstrings instead of inventing them.
        """
        import inspect

        tools = self.agent._function_toolset.tools
        out: list[dict] = []
        for name in sorted(tools.keys()):
            t = tools[name]
            try:
                src = inspect.getsource(t.function)
            except (OSError, TypeError):
                src = ""
            doc = (t.description or "").strip()
            doc_lower = doc.lower()
            operator_only = "_is_owner(" in src or any(
                marker in doc_lower
                for marker in (
                    "owner-only",
                    "only the bot's owner",
                    "operator-only",
                    "only @",
                )
            )
            out.append(
                {
                    "name": name,
                    "description": doc,
                    "operator_only": operator_only,
                }
            )
        return out

    def _mcp_toolsets(self) -> list[MCPServerStreamableHTTP]:
        """Create fresh MCP server instances for a single agent run."""
        toolsets: list[MCPServerStreamableHTTP] = [
            MCPServerStreamableHTTP(
                url="https://pdsx-by-zzstoatzz.fastmcp.app/mcp",
                timeout=30,
                headers={
                    "x-atproto-handle": settings.bluesky_handle,
                    "x-atproto-password": settings.bluesky_password,
                },
            ),
            MCPServerStreamableHTTP(
                url="https://pub-search-by-zzstoatzz.fastmcp.app/mcp",
                timeout=30,
                tool_prefix="pub",
            ),
            # Semble code-mode server (search/get_schema/execute). Keyless =
            # public reads only; the header makes writes attribute to phi.
            MCPServerStreamableHTTP(
                url=settings.semble_mcp_url,
                timeout=30,
                tool_prefix="semble",
                headers=(
                    {"x-semble-api-key": settings.semble_api_key}
                    if settings.semble_api_key
                    else {}
                ),
            ),
        ]
        # Prefect MCP — only included when auth is configured, so phi degrades
        # gracefully in dev/local without the secret set.
        if settings.prefect_api_auth_string:
            toolsets.append(
                MCPServerStreamableHTTP(
                    url=settings.prefect_mcp_url,
                    timeout=30,
                    tool_prefix="prefect",
                    headers={
                        "x-prefect-api-url": settings.prefect_api_url,
                        "x-prefect-api-auth-string": settings.prefect_api_auth_string,
                    },
                )
            )
        return toolsets

    async def _run_agent(
        self,
        *,
        label: str,
        prompt: str | list,
        deps: PhiDeps,
    ) -> str:
        """Run phi with fresh MCP toolsets and consistent error logging."""
        toolsets = self._mcp_toolsets()
        try:
            async with contextlib.AsyncExitStack() as stack:
                for ts in toolsets:
                    await stack.enter_async_context(ts)
                result = await self.agent.run(prompt, deps=deps, toolsets=toolsets)
        except Exception as e:
            err_type = type(e).__name__
            logger.exception(f"agent.run failed during {label}: {err_type}: {e}")
            return f"{label} failed: {err_type}: {str(e)[:200]}"

        summary = result.output or ""
        logger.info(f"{label} finished: {summary[:200]}")
        return summary

    async def process_notifications(
        self,
        notifications_context: dict,
        author_lookups: dict[str, str] | None = None,
        image_urls_by_uri: dict[str, list[str]] | None = None,
    ) -> str:
        """Run the agent over a batch of notifications.

        The unit of work is "the set of new notifications since the last poll."
        The agent looks at all of them at once, decides what (if anything) to do
        about each, and acts via the trusted posting tools (post / like_post /
        repost_post). Side effects happen as tool calls during the run; the
        return value is just a summary string for logging.

        notifications_context: dict mapping post URI -> per-notification context
            (cid, reason, author, text, thread refs, etc). Built by the handler.
        author_lookups: pre-fetched stranger lookups keyed by author handle.
        image_urls_by_uri: optional map of post URI -> image URLs for vision.
        """
        if not notifications_context:
            logger.info("process_notifications: empty batch, nothing to do")
            return ""

        author_count = len(
            {
                e.get("author_handle")
                for e in notifications_context.values()
                if e.get("author_handle")
            }
        )
        logger.info(
            f"processing notifications batch: {len(notifications_context)} items, "
            f"{author_count} unique authors"
        )

        deps = PhiDeps(
            author_handle="",
            memory=self.memory,
            notifications_context=notifications_context,
        )

        # User prompt is a short task instruction — the actual notifications
        # block is rendered via the inject_notifications dynamic system prompt.
        # Images from any post in the batch are attached as multimodal inputs.
        prompt_text = (
            "process your new notifications batch. look at the [NEW NOTIFICATIONS] "
            "block in your context, decide what to do, and use the trusted posting "
            "tools to act — `post(text, in_reply_to=<uri>)` for replies, "
            "`post(text)` for top-level, both with optional threading off your own "
            "posts. you don't have to act on every item — silence is fine."
        )
        if author_lookups:
            prompt_text += "\n\n" + "\n\n".join(author_lookups.values())

        user_prompt: str | list = prompt_text
        all_image_urls: list[str] = []
        if image_urls_by_uri:
            for urls in image_urls_by_uri.values():
                all_image_urls.extend(urls)
        if all_image_urls:
            user_prompt = [prompt_text] + [ImageUrl(url=u) for u in all_image_urls]
            logger.info(f"including {len(all_image_urls)} images in batch prompt")

        return await self._run_agent(
            label="batch processing",
            prompt=user_prompt,
            deps=deps,
        )

    async def _recent_conversations_block(self, top_k: int = 10) -> str:
        """Render recent interactions once for scheduled paths that need texture."""
        if not self.memory:
            return ""
        try:
            recent = await self.memory.get_recent_interactions(top_k=top_k)
        except Exception as e:
            logger.warning(f"failed to get recent interactions: {e}")
            return ""
        if not recent:
            return "[RECENT CONVERSATIONS]: no recent interactions"

        unique_handles = {i["handle"] for i in recent}
        lines = [
            f"[RECENT CONVERSATIONS]: {len(recent)} interactions with "
            f"{len(unique_handles)} people recently"
        ]
        for i in recent[:5]:
            lines.append(f"- with @{i['handle']}: {i['content'][:150]}")
        return "\n".join(lines)

    async def _run_scheduled(
        self,
        *,
        name: str,
        task: str,
        context_blocks: list[str] | None = None,
    ) -> str:
        """Run a scheduled cognitive pass with path-specific context in the prompt."""
        logger.info(f"processing {name}")
        prompt = task
        blocks = [b for b in (context_blocks or []) if b]
        if blocks:
            prompt += "\n\n" + "\n\n".join(blocks)
        return await self._run_agent(
            label=name,
            prompt=prompt,
            deps=PhiDeps(author_handle="", memory=self.memory),
        )

    async def process_reflection(self) -> str:
        """Generate a daily reflection post from recent memory."""
        context_blocks = [await self._recent_conversations_block()]
        try:
            service_health = await _check_services_impl()
        except Exception:
            service_health = ""
        if service_health:
            context_blocks.append(f"[SERVICE HEALTH]:\n{service_health}")

        return await self._run_scheduled(
            name="daily reflection",
            task=(
                "end of day. post a reflection if you have one, or don't. "
                "use [RECENT OPERATIONS] to avoid repeating what you've "
                "already posted.\n\n"
                "before posting, if today changed where a goal or interest "
                "stands — what you did, where it is now, or the next step — "
                "update one via update_goal_progress."
            ),
            context_blocks=context_blocks,
        )

    async def process_cycle(self) -> str:
        """One cognitive moment — phi assembles every signal she has and
        decides at most one thing to surface (or stays silent).

        Replaces the older separate scheduled paths (musing / relay_check /
        prefect_check). Those were three parallel agent runs, each producing
        their own post from their own slice of phi's mind, which meant the
        operator sometimes got two disconnected commentaries in the same
        minute — one about, say, mushrooms, one about a workflow failure.
        One cycle = one integrated read.
        """
        context_blocks: list[str] = []

        try:
            wf = await get_workflow_state_block()
            if wf:
                context_blocks.append(wf)
        except Exception as e:
            logger.warning(f"workflow state fetch failed: {e}")

        try:
            rfm = await get_recent_flow_mentions_block(bot_client)
            if rfm:
                context_blocks.append(rfm)
        except Exception as e:
            logger.warning(f"recent flow mentions fetch failed: {e}")

        convs = await self._recent_conversations_block(top_k=5)
        if convs:
            context_blocks.append(convs)

        task = (
            "you have a moment. one cycle — at most one post (or one thread, "
            "or silence). pick the single thread most worth surfacing now.\n\n"
            "first, scan [GOALS AND INTERESTS]. if one has a concrete next "
            "step and nothing urgent is competing (a broken/stuck workflow, "
            "someone genuinely waiting on a reply), take that step — or call "
            "update_goal_progress to record where it stands and why you're "
            "not advancing it now. a stalled line there is a real signal.\n\n"
            "what's available to look at:\n"
            "- [WORKFLOW STATE] — ground truth on the operator's infrastructure, "
            "anchored to [NOW]. deterministic synthesis of flow run history.\n"
            "- [RECENT FLOW MENTIONS] — what you've already said about workflow "
            "state recently, so you can avoid repeating yourself.\n"
            "- your [RECENT CONVERSATIONS] sitting in your context already.\n"
            "- your owned feeds, the timeline, the discovery pool, the network, "
            "the open web — call tools to pull more.\n"
            "- relay state via check_relays if it feels worth checking.\n\n"
            "rules of engagement:\n"
            "- one integrated read, one decision. if two threads both want "
            "attention (say, a workflow failure AND something you noticed "
            "reading), either braid them into one post if they connect, or "
            "pick the one that matters more and skip the other. never two "
            "disconnected posts in the same cycle.\n"
            "- workflow state: each line in [WORKFLOW STATE] reads "
            "`- name: LATEST_RUN_STATE when [classification — qualifier]`. the "
            "LATEST_RUN_STATE is the load-bearing fact. the bracketed "
            "classification is one of four labels, each with a fixed "
            "response:\n"
            "    * [broken]    most recent terminal failed. tag the operator "
            "once if [RECENT FLOW MENTIONS] doesn't already cover it. don't "
            "re-tag while it stays broken — they've heard you.\n"
            "    * [stuck]     a run isn't being picked up (PENDING/RUNNING "
            "past expected start). tag immediately if not already covered.\n"
            "    * [degraded]  recent runs flapped but the most recent one "
            "completed. don't tag — degraded is not broken. degraded is not "
            "stuck — but if the flap pattern is newly noteworthy, use "
            "save_memory so a later cycle can find it with search_memory. "
            "don't say 'still stuck' or 'still "
            "broken' for a degraded deployment.\n"
            "    * [healthy]   silence.\n"
            "  use the classification label verbatim. don't substitute. and "
            "SCHEDULED runs with a future expected_start_time are normal "
            "scheduler calendar — never a backlog or 'queue'.\n"
            "- relay state: post about transitions only when a *.waow.tech "
            "relay is degraded or worse, OR the whole fleet is degraded or "
            "worse. otherwise use save_memory to record the change in your "
            "own words so a later cycle can find it with search_memory, "
            "rather than posting it.\n"
            "- silence is usually right."
        )

        return await self._run_scheduled(
            name="cycle",
            task=task,
            context_blocks=context_blocks,
        )

    async def process_extraction(self) -> int:
        """Review recent unprocessed interactions and extract observations. Returns count stored."""
        if not self.memory:
            return 0

        unprocessed = await self.memory.get_unprocessed_interactions(top_k=20)
        if not unprocessed:
            logger.info("extraction: no unprocessed interactions")
            return 0

        logger.info(
            f"extraction: reviewing {len(unprocessed)} unprocessed interactions"
        )

        # group by handle
        by_handle: dict[str, list[InteractionRow]] = {}
        for interaction in unprocessed:
            by_handle.setdefault(interaction["handle"], []).append(interaction)

        total_stored = 0
        for handle, interactions in by_handle.items():
            exchange_texts = [i["content"] for i in interactions]
            # collect every URI cited by the interactions in this batch.
            # the extraction agent doesn't see URIs (only the exchange text),
            # so we attribute *every* extracted observation in this batch to
            # *all* the URIs that fed it. coarse, but always-true: an
            # observation extracted from this batch was justified by
            # something in this batch. dedup-preserve-order.
            batch_uris = list(
                dict.fromkeys(uri for i in interactions for uri in i["source_uris"])
            )
            prompt = f"recent exchanges with @{handle}:\n\n" + "\n\n---\n\n".join(
                exchange_texts
            )

            try:
                result = await self._extraction_agent.run(prompt)
                if result.output.observations:
                    for obs in result.output.observations:
                        # inherit URIs from the interactions that sourced
                        # this batch unless the model already filled them in
                        if not obs.source_uris and batch_uris:
                            obs.source_uris = list(batch_uris)
                        try:
                            await self.memory._reconcile_observation(handle, obs)
                            total_stored += 1
                        except Exception as e:
                            logger.warning(f"reconciliation failed: {e}")
            except Exception as e:
                logger.warning(f"extraction failed for @{handle}: {e}")

        return total_stored

    async def process_bio(self) -> str:
        """Ask phi to rewrite her bsky bio via the main-agent write_bio tool.

        Running through the main agent gives the bio pass the same dynamic
        context blocks as normal operation, especially [OPERATOR]. The
        write_bio tool owns the actual profile write and 256-char validation.
        """
        logger.info("processing bio rewrite")
        return await self._run_agent(
            label="bio rewrite",
            prompt=(
                "rewrite your bsky profile bio. call write_bio with the final "
                "text. use [OPERATOR] for the operator handle; do not guess. "
                "structural max is 256 characters."
            ),
            deps=PhiDeps(author_handle="", memory=self.memory),
        )
