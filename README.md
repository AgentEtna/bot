# phi

a bluesky bot. listens, decides, posts, remembers, watches a few things in the background. personality is [public](personalities/phi.md).

```
notifications ── ┐
schedule ─────── ├─→ phi (pydantic-ai) ─→ tools ─→ atproto / web / pds / memory
self-state ───── ┘
```

phi reads its own state (recent posts, goals, what it's pending, what's relevant from memory), looks at what's in front of it, and decides whether to act. actions happen as tool calls inside the agent run — there's no separate dispatch layer. public actions pass through a safety layer (written policies, an independent judge, an operator override) — see [docs/safety.md](docs/safety.md).

## stack

- [pydantic-ai](https://ai.pydantic.dev/) for the agent loop and tool surface
- [atproto](https://atproto.com) for everything social — posts, follows, threads, the firehose
- [mcp](https://modelcontextprotocol.io/) for external capabilities (atproto record CRUD, publication search, the semble knowledge graph, prefect workflow state)
- [turbopuffer](https://turbopuffer.com/) for private vector memory
- [cosmik](https://cosmik.network) / [semble](https://semble.so) for public knowledge that anyone can discover
- [tavily](https://tavily.com) for grounding in current web sources
- [fly.io](https://fly.io) for hosting

## quick start

```bash
uv sync
cp .env.example .env  # edit with your credentials
just run
```

required: `BLUESKY_HANDLE`, `BLUESKY_PASSWORD`, `ANTHROPIC_API_KEY`. see `.env.example` for the optional knobs.

## development

```bash
just run        # run bot
just dev        # hot-reload
just check      # lint + typecheck + test
just evals      # behavioral tests
just deploy     # fly.io (the only deploy path)
```

## docs

- [architecture](docs/architecture.md) — data flow, scheduling, which model runs which agent, why the design
- [memory](docs/memory.md) — thread context, private memory, public memory, how they compose
- [system-prompt](docs/system-prompt.md) — every block in phi's context, where it comes from, when it refreshes
- [mcp](docs/mcp.md) — how external tool servers are integrated
- [safety](docs/safety.md) — policies, the judge, the operator override
- [testing](docs/testing.md) — testing philosophy

## reference projects

[void](https://tangled.sh/@cameron.pfiffer.org/void.git), [penelope](https://github.com/haileyok/penelope), [prefect-mcp-server](https://github.com/PrefectHQ/prefect-mcp-server).
