from typing import Literal, Self

from atproto_client.models.string_formats import Did, Handle
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bot.logging_config import setup_logging


class LogfireSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LOGFIRE_", extra="ignore", env_file=".env"
    )

    write_token: str | None = None
    # read token (project-scoped, query-only) — when set, phi gets the
    # logfire MCP query tools and can read her own traces
    read_token: str | None = None
    # org API key with the "Alerts management" scope — when set, the poller
    # watches every project's alerts (core/alert_watch.py). read-only use.
    alerts_token: str | None = None
    environment: str | None = None
    send_to_logfire: Literal["if-token-present"] | None = "if-token-present"
    # project UI root, used to deep-link a run to its trace from the
    # cockpit. the SDK doesn't expose this without a credentials fetch, so
    # it's configuration; set to None to render the cockpit without links.
    ui_url: str | None = "https://logfire-us.pydantic.dev/waow/phi"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Bluesky credentials
    bluesky_handle: str = Field(
        default=..., description="The handle of the Bluesky account"
    )
    bluesky_password: str = Field(
        default=..., description="The password of the Bluesky account"
    )
    bluesky_service: str = Field(
        default="https://bsky.social",
        description="The service URL of the Bluesky account",
    )

    # Bot configuration
    bot_name: str = Field(default="Bot", description="The name of the bot")
    personality_file: str = Field(
        default="personalities/phi.md",
        description="The file containing the bot's personality",
    )
    skills_dir: str = Field(
        default="skills",
        description="Directory containing agentskills.io-format skill packages",
    )
    web_build_dir: str = Field(
        default="/app/web",
        description=(
            "Directory containing the built sveltekit frontend (adapter-static "
            "output). Mounted at / by the FastAPI app when present."
        ),
    )

    # LLM configuration (support multiple providers)
    openai_api_key: str | None = Field(
        default=None, description="The API key for the OpenAI API"
    )
    anthropic_api_key: str | None = Field(
        default=None, description="The API key for the Anthropic API"
    )
    # Reads the github mirror of this repo for check_infra(aspect="changelog").
    # Unauthenticated, the GitHub API allows 60 requests/hour *per IP* — one
    # busy run can exhaust it, and phi's fly machine does not have that IP to
    # itself. A token raises the ceiling to 5,000/hour. Optional: without one
    # the call still works, it just rate-limits under load.
    github_token: str | None = Field(
        default=None, description="GitHub token for the changelog API (read-only scope)"
    )
    # Tavily web search — grounds phi against the open web for currency
    # checks and source-finding. Free tier covers 1k searches/month.
    tavily_api_key: str | None = Field(
        default=None, description="API key for Tavily web search"
    )

    # TurboPuffer configuration
    turbopuffer_api_key: str | None = Field(
        default=None, description="The API key for the TurboPuffer API"
    )
    turbopuffer_region: str = Field(
        default="gcp-us-central1", description="The region for the TurboPuffer API"
    )

    # Model configuration
    agent_model: str = Field(
        default="anthropic:claude-sonnet-5",
        description="Model for the main agent (pydantic-ai model string)",
    )
    # Sub-agent models are full pydantic-ai `provider:model` strings, same as
    # agent_model. OpenAI reasoning models must use the `openai-responses:`
    # prefix: /v1/chat/completions rejects function tools alongside
    # reasoning_effort, and every sub-agent here has an output_type, which
    # pydantic-ai sends as a function tool.
    extraction_model: str = Field(
        default="openai-responses:gpt-5.6-luna",
        description="Model for the extraction/synth sub-agents",
    )
    policy_model: str = Field(
        default="openai-responses:gpt-5.6-luna",
        description="Model for the pre-action policy judge (bot.core.policy)",
    )

    # Server configuration
    host: str = Field(default="0.0.0.0", description="The host for the server")
    port: int = Field(default=8000, description="The port for the server")

    # Polling configuration
    notification_poll_interval: int = Field(
        default=10, description="The interval for polling for notifications"
    )
    alert_poll_interval: int = Field(
        default=3600,
        description="Seconds between logfire alert-state reconciliation "
        "polls — firings arrive by webhook push (/api/alerts); the poll "
        "catches missed deliveries and drives quiet-close",
    )
    alert_webhook_token: str | None = Field(
        default=None,
        description="Shared secret in the /api/alerts webhook URL; the "
        "endpoint 401s everything when unset",
    )
    alert_projects: list[str] = Field(
        default_factory=list,
        description="Logfire projects to watch; empty means all readable",
    )

    # Repo ops awareness — jetstream tail of phi's own PDS commits
    jetstream_urls: tuple[str, ...] = Field(
        default=(
            "wss://jetstream1.us-west.bsky.network/subscribe",
            "wss://jetstream2.us-west.bsky.network/subscribe",
            "wss://jetstream1.us-east.bsky.network/subscribe",
            "wss://jetstream2.us-east.bsky.network/subscribe",
        ),
        description=(
            "Jetstream endpoints for tailing repo commits, rotated on every "
            "reconnect. One instance alone is a single point of silence: on "
            "2026-08-21 jetstream2.us-east delivered nothing for two watched "
            "DIDs for 40 minutes while its siblings carried the events."
        ),
    )
    ops_log_path: str = Field(
        default="/data/ops_log.jsonl",
        description="Durable append-only log of phi's repo operations",
    )

    # Operator timezone — drives schedule slots + the local-time line in
    # phi's [NOW] block. Lives on the operator's clock so phi posts at
    # human-friendly times of day regardless of DST shifts.
    operator_timezone: str = Field(
        default="America/Chicago",
        description=(
            "IANA timezone name for the operator's local time. Schedule hours "
            "below are interpreted in this zone; phi sees this in her [NOW] "
            "block so she knows whose clock she's on."
        ),
    )

    # Daily reflection — local hour in operator_timezone
    daily_reflection_hour: int = Field(
        default=9,
        description="Hour-of-day (operator local time) to post the daily reflection",
    )

    # Original thought posts — local hours in operator_timezone. 4 slots
    # spread across the operator's waking hours (9am, 1pm, 5pm, 9pm), every
    # 4h. Quiet enough to feel deliberate rather than noisy.
    thought_post_hours: list[int] = Field(
        default=[9, 13, 17, 21],
        description=(
            "Hours-of-day (operator local time) to attempt original thought "
            "posts. Each slot fires at most once."
        ),
    )

    # Which of the thought slots go to people instead of a general cycle.
    # Every other scheduled wake points phi at machine state, so without
    # this nothing ever sends her to read a person. A subset rather than a
    # new cadence: same rhythm, different attention.
    people_pass_hours: list[int] = Field(
        default=[17],
        description=(
            "Hours-of-day (operator local time) whose thought slot runs the "
            "people pass instead of a general cycle. Must be a subset of "
            "thought_post_hours to fire at all."
        ),
    )

    # External feeds phi can read
    saved_feeds: dict[str, str] = Field(
        default={
            "for-you": "at://did:plc:3guzzweuqraryl3rdkimjamk/app.bsky.feed.generator/for-you",
        },
        description="friendly name → AT-URI for external feeds phi can read",
    )
    # Control API
    control_token: str | None = Field(
        default=None, description="Bearer token for /api/control endpoints"
    )

    # Owner identity — handle or DID. Resolved to a profile (with display
    # name) at runtime via the atproto SDK; see core.operator.
    owner_did: Did = Field(
        default="did:plc:xbtmt2zjwlrfegqvch7fboei",
        description=(
            "Operator DID whose io.zzstoatzz.phi.override record the bot "
            "obeys (safe mode). A did:plc is permanent, so this is a "
            "deploy-time constant — no runtime handle resolution."
        ),
    )
    owner_handle: Handle | Did = Field(
        default="zzstoatzz.io",
        description="Handle or DID of the bot's owner (permission-gated tools)",
    )
    review_poll_interval: int = Field(
        default=60,
        description=(
            "Seconds between polls of the reviewers' PDSes for new comments on "
            "phi's pull requests — the authoritative path; jetstream is the fast one."
        ),
    )
    reviewer_dids: tuple[Did, ...] = Field(
        default=(
            "did:plc:xbtmt2zjwlrfegqvch7fboei",  # zzstoatzz.io
            "did:plc:mkqt76xvfgxuemlwlx6ruc3w",  # zat.dev
            "did:plc:o53crari67ge7bvbv273lxln",  # zzstoatzzdevlog.bsky.social
        ),
        description=(
            "DIDs whose pull-request comments on phi's repo wake her. The "
            "operator has more than one identity and reviews from whichever "
            "is logged in (2026-08-21: a review left as zat.dev went nowhere)."
        ),
    )

    # Relay fleet monitoring — phi polls relay-eval on a schedule and
    # reports status transitions. The service is the source of truth;
    # phi is the courier. This is the base URL; /history and /events
    # are derived from it.
    relays_url: str = Field(
        default="https://relay-eval.waow.tech/api/relays",
        description="Base URL for relay-eval's relay API (snapshot endpoint)",
    )
    relay_watch_interval: int = Field(
        default=900,
        description="Seconds between relay-eval behind-the-network checks; "
        "0 disables. relay-eval evaluates the fleet roughly every 30 "
        "minutes, so 15 minutes bounds detection latency at about one "
        "eval run",
    )
    relay_watch_hosts: list[str] = Field(
        default=[
            "zlay.waow.tech",
            "relay.waow.tech",
            "stream.waow.tech",
            "jetstream.waow.tech",
        ],
        description="Relay hosts whose behind-the-network regressions open "
        "incidents — the operator's own relays, not the whole fleet "
        "(chronically-behind third-party relays would be permanent noise)",
    )

    # Prefect flow monitoring — phi polls the prefect-server via the prefect
    # MCP to notice failed/crashed flows (ingest, brief, compact, etc.) and
    # flag persistent failures to the operator. Same pattern as relays.
    prefect_mcp_url: str = Field(
        default="https://prefect-by-zzstoatzz.fastmcp.app/mcp",
        description="URL of the prefect MCP server (fastmcp.app deployment)",
    )
    prefect_api_url: str = Field(
        default="https://prefect-server.waow.tech/api",
        description="Prefect OSS API URL (passed to MCP via x-prefect-api-url header)",
    )
    prefect_api_auth_string: str | None = Field(
        default=None,
        description=(
            "Basic auth string 'user:pass' for prefect OSS. Passed to MCP via "
            "x-prefect-api-auth-string header. Set via fly secret."
        ),
    )
    # Semble — phi's public knowledge graph (network.cosmik.* records),
    # reached through the hosted code-mode MCP. The server is stateless:
    # identity arrives per-request via the x-semble-api-key header. Without
    # a key phi still gets the full public read surface; the key (a fly
    # secret, minted from phi's account at semble.so/settings/api-keys)
    # enables writes attributed to phi.
    semble_mcp_url: str = Field(
        default="https://semble.fastmcp.app/mcp",
        description="URL of the semble MCP server (fastmcp.app deployment)",
    )
    semble_api_key: str | None = Field(
        default=None,
        description="Phi's semble API key (enables writes; omit for read-only)",
    )

    # Tangled — git collaboration on atproto. Reads go through bobbin
    # (tangled's public API) with no auth; writes are atproto records on
    # phi's own PDS, so phi's bluesky credentials ride along per-request
    # via x-tangled-* headers and issues/comments attribute to phi.
    tangled_mcp_url: str = Field(
        default="https://nate-tangled-mcp.fastmcp.app/mcp",
        description="URL of the tangled MCP server (fastmcp.app deployment)",
    )

    # Lexidraw — drawings as app.lexidraw.scene records in phi's own repo,
    # via the stdio node MCP server baked into the image. Absent path (dev,
    # local) simply leaves the toolset out.
    lexidraw_mcp_path: str = Field(
        default="/opt/lexidraw/dist/index.js",
        description="path to the built lexidraw-mcp entrypoint; toolset is skipped if missing",
    )

    # Discovery pool — generic agents endpoint serving authors the operator
    # has been liking. Currently lives on hub.waow.tech as part of the
    # prefect-server side; consumers (phi here) read it as opaque JSON.
    discovery_pool_url: str = Field(
        default="https://hub.waow.tech/api/agents/discovery-pool",
        description="URL of the discovery-pool JSON endpoint (operator-likes derived)",
    )

    # Debug mode
    debug: bool = Field(default=True, description="Whether to run in debug mode")

    # Logfire
    logfire: LogfireSettings = Field(default_factory=LogfireSettings)

    @model_validator(mode="after")
    def configure_logging(self) -> Self:
        """Configure stdlib logging."""
        setup_logging(debug=self.debug)
        return self


settings = Settings()
