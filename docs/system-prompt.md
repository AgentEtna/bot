# system prompt

what's actually injected into phi's context on every agent run, where it comes from, and when it refreshes.

phi is a [pydantic-ai](https://ai.pydantic.dev/) agent. its system prompt is composed of a static base plus a set of dynamic blocks contributed by `@agent.system_prompt(dynamic=True)` functions, all in `src/bot/agent.py`. tool definitions are surfaced separately by the framework — phi sees each tool's docstring and signature without us having to repeat them in the prompt.

## composition

| block | source | refreshes | purpose |
|---|---|---|---|
| **personality + operational rules** | static (`personalities/phi.md` + `_build_operational_instructions()`) | process restart | who phi is + cross-cutting rules (consent, ownership, memory trust hierarchy, posting tools) |
| **`[YOUR INFRASTRUCTURE]`** | `inject_identity` → `bot_client.client.me` | every run | handle / DID / PDS host so phi knows its own identity |
| **`[NOW]`** | `inject_today` | every run | current UTC timestamp |
| **`[KNOWN RELAYS]`** | `inject_known_relays` → `tools.bluesky.fetch_relay_names()` (5min TTL) | every 5min | exact relay hostnames for `check_relays(name=...)` so the LLM picks valid values |
| **`[GOALS]`** | `get_state_block` → PDS `io.zzstoatzz.phi.goal` (5min block cache) | every 5min | phi's anchors. mutated via `propose_goal_change` (owner-gated) |
| **`[ACTIVE OBSERVATIONS]`** | `inject_active_observations` → PDS `io.zzstoatzz.phi.observation` | every run | phi's small attention pool (max 5). things noticed, not yet acted on. mutated via `observe` / `drop_observation`. older items age out into a turbopuffer archive (`phi-observations` namespace) — not in the prompt, but searchable via `recall` (returned with `[archived observation: <reason>]` label so the lifecycle stays legible) |
| **`[INNER CRITIC]`** | `get_state_block` → haiku pass over recent posts + goals (1h cache, invalidated by new post) | when posts change or 1h elapses | phi's own voice turned inward — first-person critique of patterns to push against, drift from goals, jargon not earning its space. not an external audit; self-knowledge |
| **`[SELF STATE]`** | `get_state_block` → PDS reads (5min) | every 5min | last-follow age (more pointers can be added here as needed) |
| **`[RECENT OPERATIONS]`** | `get_operations_block` → `list_records` per meaningful collection, merged by rkey desc (5min cache) | every 5min | last 10 PDS writes across collections (post / like / follow / goal / cosmik card / cosmik connection / greengale doc), chronological. continuity signal — phi sees what it's actually been doing |
| **`[DISCOVERY POOL]`** | `get_discovery_pool_block` → http GET to `discovery_pool_url` (hub) → filter out handles with prior interactions (5min cache) | every 5min | strangers the operator has been liking lately, with sample posts. high-signal warm leads — service-owned data (hub reads operator's likes from duckdb), phi-side filter (per-author interaction check) |
| **`[NEW NOTIFICATIONS]`** | `inject_notifications` ← `PhiDeps.notifications_context` | per batch | the unread notifications grouped by thread |
| **`[USER CONTEXT]` / `[PHI'S SYNTHESIZED IMPRESSION]` / `[OBSERVATIONS]` / `[PAST EXCHANGES]` / `[BACKGROUND RESEARCH]`** | `inject_user_memory` → turbopuffer `phi-users-{handle}` per author in batch | per batch | per-author memory blocks, labeled by trust level. impression is synthesized by an external prefect flow; observations are extracted by the haiku extraction agent |
| **`[RELEVANT MEMORIES — synthesized for this query]`** | `inject_episodic` → top-K from turbopuffer `phi-episodic` → haiku synthesis given goals + query | per batch | a coherent block (deduped, recency-aware) instead of a raw similarity-ranked dump. flags stale entries when present |
| **`[FIRST INTERACTION WITH @author]`** | `inject_author_lookups` ← `PhiDeps.author_lookups` (pre-fetched by handler) | per batch when author is unfamiliar | profile + recent posts so phi has signal on a stranger before deciding to engage |
| **`[SEMBLE]`** | `inject_public_memory` → cosmik record count | every run | one-line reminder phi has public collections via cosmik/semble |

## design rules

**docstrings, not prompt restatement.** the framework surfaces tool docstrings to the model. anything we put in the system prompt that re-describes a tool drifts when the tool changes — so we put per-tool guidance in the docstring and keep the prompt for cross-cutting rules (consent, owner gates, memory trust hierarchy).

**identifiers in the block.** `[KNOWN RELAYS]` puts exact hostnames in the label so phi can't hallucinate. `[GOALS]` puts the NSID + rkey in the label so phi can call `propose_goal_change(rkey=...)` correctly. mirrors the same pattern: when phi needs to reference a thing, surface the exact identifier where it'll be used.

**synthesize before injecting where shape matters.** raw top-K from a vector store ranks by cosine similarity, which doesn't reconcile contradictions or note recency. for blocks where the model needs a *coherent* view (recent posts → audit, episodic candidates → relevant memories), a small haiku pass takes the candidates plus context and produces a block phi can act on directly.

**cache canonical reads, not derived ones (separately).** PDS reads (goals, queue depth, last follow) are cheap-but-not-free; cache the whole `[GOALS]+[AUDIT]+[SELF STATE]` block at 5min so 10s-cadence notification polls don't hammer PDS. haiku passes that depend on phi's posts cache longer (1h) and invalidate on new-post-URI change.

**empty-when-unset.** dynamic blocks return `""` when their `PhiDeps` field is missing (e.g. `last_post_text` only set during musing/reflection). pydantic-ai includes empty parts as zero-token slots — minor cost, zero signal.

## audit it

the system prompt for any specific run is captured by pydantic-ai's logfire integration. query the `agent run` span where `gen_ai.agent.name = 'phi'` — `attributes.pydantic_ai.all_messages[0]` is the full system message, with each dynamic block as a separate `text` part.
