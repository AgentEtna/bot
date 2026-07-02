phi — a bluesky bot. python + pydantic-ai + atproto + turbopuffer + cosmik/semble. fastapi for the small web surface (status pages, memory graph).

## development

- `just run` / `just dev` (hot-reload) / `just deploy` (manual fly.io) / `just release X` (tag vX, CI deploys)
- `just check` — lint + typecheck + test
- `just evals` — behavioral tests (llm-as-judge)
- `just loq-relax <file>` — relax line limit for a file. never edit loq.toml manually or compress code to fit
- work from repo root

## python style

- 3.10+ typing (`T | None`, `list[T]`)
- prefer functional over OOP
- imports at the top — no deferred imports unless circular
- never use `pytest.mark.asyncio`
- tool params: `Annotated[T, Field(description=...)]` so the LLM sees what each param does

## project structure

```
src/bot/
├── agent.py               # pydantic-ai agent + dynamic system prompts
├── config.py              # settings (env vars)
├── main.py                # fastapi app, status pages, memory graph
├── status.py              # runtime metrics
├── core/                  # atproto client, profile, mentionable, goals, self_state
├── memory/                # turbopuffer namespaces + extraction/reconciliation/review pipelines
├── services/              # notification polling + message handler
├── tools/                 # native pydantic-ai tools (posting, search, memory, goals, blog, etc.)
└── utils/                 # thread context, text formatting

web/                       # sveltekit cockpit at phi.zzstoatzz.io (internal — docs/internal/cockpit.md)
lexicons/                  # custom io.zzstoatzz.phi.* lexicon definitions
docs/                      # deeper reference (docs/README.md has the reading order)
personalities/             # personality definitions (public; phi.md is the live one)
skills/                    # phi's runtime skills (loaded by the agent at run time)
.claude/skills/            # operator-facing skills (for the human + claude code working on phi)
evals/                     # behavioral tests
scripts/                   # proven utility scripts
sandbox/                   # experiments (graduate to scripts/ once proven)
.eggs/                     # cloned reference projects
```

## key architecture

- one agent loop, many entry points (notifications batch, cycle, daily reflection). all end in `agent.run()` with different `PhiDeps`.
- actions happen as tool calls inside the run, not via structured output. the agent's return value is a brief summary string for logging.
- public actions are gated (docs/safety.md): written policies + an independent judge on every `post` (`core/policy.py`), a structural guard refusing raw `app.bsky.feed.*` writes via pdsx (`core/mcp_guard.py`), and an operator override — an `io.zzstoatzz.phi.override` record on the *operator's* repo that makes post/like/repost refuse while active (`core/override.py`, editable at the cockpit's `/operator`).
- personality is separate from operational rules. tool docstrings carry per-tool guidance, not the system prompt.
- memory: turbopuffer namespaces (`phi-users-{handle}`, `phi-episodic`). intent state on PDS under `io.zzstoatzz.phi.*` (goals, mention consent, legacy queue).
- owner-gated mutations (`follow_user`, `propose_goal_change`, `manage_mentionable`, `create_feed`) flow through a like-as-approval mechanism: phi posts an authorization request, owner likes it, next batch lets the action through.
- MCP servers: pdsx (atproto record CRUD, feed-writes guarded), pub-search (publication search), semble (code-mode public knowledge graph), prefect (workflow state; only when auth configured). connected via `MCPServerStreamableHTTP`, fresh per `agent.run()`.
- web grounding via tavily for recency claims (`web_search`).

## skills

two distinct namespaces — don't confuse them:

- `skills/` — **phi's runtime skills.** loaded by the agent during `agent.run()`. these are things *phi* does: `publish-blog`, `cosmik-records`, `pdsx-fundamentals`, `phi-prompt-inspect`.
- `.claude/skills/` — **operator-facing skills.** for the human + claude code working *on* phi, not phi itself. these are things *you* do to inspect or talk to phi:
  - `phi-check` — inspect phi's health/activity via logfire, bsky, fly, PDS.
  - `devlog-to-phi` — post a message to phi from the operator's devlog account (the pdsx MCP is already authed as devlog; it's a thin record-create recipe, no script).

when you add a capability, decide which namespace it belongs to and document it here. prefer the pdsx MCP over hand-rolled scripts for any atproto write — pdsx is authenticated as the devlog account (`mcp__pdsx__whoami` to confirm).

## documentation

deeper reference in `docs/`:
- `architecture.md` — entry points, scheduling, why this shape
- `memory.md` — the four kinds of state and how they compose
- `system-prompt.md` — block-by-block reference for what's in phi's context
- `mcp.md` — MCP integration
- `safety.md` — the policy judge, the pdsx feed-write guard, the operator override, and the incident that shaped them
- `testing.md` — testing philosophy
- `internal/cockpit.md` — the web UI (internal, operator-facing)

`docs/system-prompt.md` is drift-checked: `tests/test_docs_sync.py` fails if an `inject_*` prompt function or a policy slug isn't mentioned there. add the row, don't weaken the test.

## deployment

fly.io app `zzstoatzz-phi`. CI deploys on `v*` tag push (tangled `.tangled/workflows/deploy.yml`). `just release X.Y.Z` tags + pushes; `just deploy` runs fly deploy directly when CI is backed up. push to both `origin` (tangled) and `github` mirror.

secrets via `fly secrets set` or `fly secrets import` (pipe `grep ^KEY .env` into it to keep values off the terminal).
