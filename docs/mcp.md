# mcp integration

phi uses the [model context protocol](https://modelcontextprotocol.io) to access external tools hosted as remote servers, connected via `MCPServerStreamableHTTP` (pydantic-ai). the authoritative list is `_mcp_toolsets` in `src/bot/agent.py`; currently: pdsx (atproto record CRUD, phi's credentials), pub-search (long-form publication search), semble (code-mode surface over phi's public knowledge graph), tangled (code collab — repos, issues, PRs), and prefect (workflow state, only when auth is configured).

## why mcp

- **separation**: tools live in external servers, not in phi's codebase
- **extensibility**: add new capabilities by connecting another server
- **reusability**: same servers can be used by other agents or tools
- **no local dependencies**: phi doesn't need to bundle client libraries for each surface

## how it works

MCP servers are created fresh per `agent.run()` call to avoid connection scope issues. the agent enters each server's async context before running, so parallel tool calls share the connection.

```python
toolsets = self._mcp_toolsets(run_label=label)
async with contextlib.AsyncExitStack() as stack:
    for ts in toolsets:
        await stack.enter_async_context(ts)
    result = await self.agent.run(prompt, deps=deps, toolsets=toolsets)
```

## process_tool_call hooks

surfaces are not permission boundaries — a server that accepts phi's credentials will do whatever the credentials allow. where the boundary matters, it lives in a `process_tool_call` hook on the toolset (`src/bot/core/mcp_guard.py`):

- **pdsx**: structural guard. raw `app.bsky.feed.*` writes refuse with a pointer to the trusted posting tools, so the consent layer / policy judge / operator override can't be bypassed (see `docs/safety.md`).
- **semble**: observational logger. every library write leaves a logfire event with the run label and executed code, so card provenance is queryable.

## notes on semble

semble's code-mode server (`search` / `get_schema` / `execute`) exposes the whole sdk behind three meta-tools — the anti-sprawl move (`docs/tool-sprawl.md`). two facts worth keeping:

- **appview writes are protocol-native.** writes through the semble api land as real `network.cosmik.*` records on the repo of the account behind the api key, and deletes propagate to the pds. verified experimentally 2026-06-11 (write via api → read the record straight off the pds → delete via api → gone from the pds).
- **`execute` with a key is arbitrary sandboxed code with the key's full read/write power.** the consent story for semble writes is norms + the write logger, not a structural gate; writes there are public and attributed to phi.

## native tools vs MCP tools

phi has two kinds of tools:

- **native tools** (defined in `src/bot/tools/`) — memory, search, trending, feeds, posting. these need direct access to phi's deps (memory client, config, etc).
- **MCP tools** (from remote servers) — stateless HTTP calls that don't need phi's internal state.

the agent sees all tools uniformly and picks the right one for the task.
