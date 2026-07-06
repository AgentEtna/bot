# system prompt

what's actually injected into phi's context on every agent run, where it comes from, and when it refreshes. audited against the live `src/bot/agent.py` injectors and the modules they call.

phi is a [pydantic-ai](https://ai.pydantic.dev/) agent. its context is composed of three layers:

1. a **static base** (personality + cross-cutting operational rules), set once at construction;
2. a set of **dynamic system-prompt blocks** contributed by `@agent.system_prompt(dynamic=True)` functions — recomposed every run;
3. **path-specific blocks** appended to the *user* message by the entry point (notifications / cycle / reflection), so they appear only on the path that needs them.

tool definitions are surfaced separately by the framework — phi sees each tool's docstring and signature without us repeating them in the prompt.

## 1. static base

set in `PhiAgent.__init__`, refreshes on process restart only:

- **personality** — `personalities/phi.md`, verbatim, prefixed "the following is your personality:".
- **operational rules** — `_build_operational_instructions()`: cross-cutting constraints no single tool docstring can own (the posting/consent layer, the memory trust hierarchy, the mention-consent allowlist, owner-like-as-approval, and the URIs-only-from-the-notifications-block rule).
- **policies** — the same function renders phi's written policies from `bot.core.policy.POLICIES` (`uninvited-reply`, `bliss-attractor`, `pile-on`), plus a note that an independent judge reviews every `post` call against them before it executes. one source: the judge and the prompt read the same dict. see [safety.md](safety.md).

tool definitions are cached at the Anthropic layer (`anthropic_cache_tool_definitions="1h"`).

## 2. dynamic system-prompt blocks (every run)

contributed by the `inject_*` callbacks in `agent.py`, in registration order. each returns `""` when its inputs are absent (pydantic-ai includes empty parts as zero-token slots — minor cost, zero signal).

| block | injector → source | refreshes | purpose |
|---|---|---|---|
| `[YOUR INFRASTRUCTURE]` | `inject_identity` → `bot_client.client.me` | every run | phi's own handle / DID / PDS host |
| `[OPERATOR OVERRIDE]` | `inject_operator_override` → `core/override.py` → `io.zzstoatzz.phi.override` record on the *operator's* repo (60s TTL) | every run while active; renders nothing when inactive | safe mode banner: the operator's message verbatim, what's refused (post/like/repost), and the channel back (PDS notes). rendered up front so phi learns about the override before bumping into tool refusals. see [safety.md](safety.md) |
| `[OPERATOR]` | `inject_operator` → `get_operator_profile` | every run | resolved owner name + handle + DID |
| `[NOW]` / `[NOW (operator local)]` | `inject_today` | every run | UTC + operator-local clock (schedule slots are anchored to operator-local) |
| `[OPERATIONAL HISTORY]` | `inject_pause_history` → `bot_status` | every run | the most recent pause/resume cycle, only while the resume is <24h old |
| `[KNOWN RELAYS]` | `inject_known_relays` → `fetch_relay_names` (5min TTL) | every 5min | exact relay hostnames so `check_relays(name=...)` can't hallucinate |
| `[GOALS AND INTERESTS]` | `inject_self_state` → `get_state_block` → PDS `io.zzstoatzz.phi.goal` (5min block cache, invalidated by either goal-mutation tool so phi sees her own writes immediately) | every 5min, or on goal mutation | goals + interests, each with current state / next step / last step, plus a "stalled" line when one hasn't been advanced for several days (`STALE_AFTER_DAYS`). constitutional fields (title/why/progress-means/kind) are owner-gated via `propose_goal_change`; operational fields are phi-writable via `update_goal_progress`. includes a live-computed progress line for the make-friends goal |
| `[SELF-AWARENESS]` | same `get_state_block` → haiku-class agent `phi-posting-inventory` over recent posts (1h cache, invalidated by new post URI) | when latest post changes or 1h elapses | structured third-person *inventory* of recent top-level posts: `subjects: … / people: … / mode: … / missing lately: …`. deliberately plain English — descriptive context, **not phi's voice**. the agent prompt explicitly forbids first person, em-dashes, abstract noun-phrases, rhetorical openings, and "not X, it's Y" constructions, because exemplar pressure beats abstract rules — if this block spoke in phi's register it would reinforce the bad voice it was meant to describe. block header also tells phi not to imitate it |
| `[SELF STATE]` | same `get_state_block` → PDS reads | every 5min | last-follow age |
| `[RECENT OPERATIONS]` | `inject_recent_operations` → `list_records` across `MEANINGFUL_COLLECTIONS`, merged by rkey desc (5min cache) | every 5min | last N PDS writes (post / like / repost / follow / goal / cosmik card / cosmik connection / greengale doc). post bodies are **intentionally stripped** — action + char count only — so this block doesn't double as voice training. titles for intentional public-anchor artifacts (goal titles, blog docs, URL card titles) are kept since those aren't posting register |
| `[DISCOVERY POOL]` | `inject_discovery_pool` → hub GET → filter handles with prior interactions (5min cache) | every 5min | strangers the operator has been liking lately — warm leads |
| `[NEW NOTIFICATIONS]` | `inject_notifications` ← `PhiDeps.notifications_context` | per batch | the unread batch grouped by thread. empty on scheduled paths |
| per-author memory — up to four independent blocks, each emitted only when that data exists: `[PHI'S SYNTHESIZED IMPRESSION OF @h]`, `[OBSERVATIONS ABOUT @h]`, `[PAST EXCHANGES WITH @h]`, `[BACKGROUND RESEARCH ON @h]`; `[USER CONTEXT - @h]` ("no previous interactions") is the fallback when none apply or the lookup errors | `inject_user_memory` → `build_user_context` per author → turbopuffer `phi-users-{h}` | per batch (one set per author in the batch) | per-author memory, labeled by trust: synthesized impression (low, may hallucinate), observations (medium), exchanges (high), exploration notes (lowest, legacy). nothing when no batch authors |
| `[RELEVANT MEMORIES — synthesized for this query]` | `inject_episodic` → `phi-episodic` top-K → haiku synthesis given goals + query | per batch | a coherent, deduped, recency-aware block instead of a raw similarity dump. only fires when a notifications seed exists |
| `[ATLAS]` | `inject_atlas_digest` → PDS `io.zzstoatzz.phi.atlas` blob (CID-cached) | when the phi-atlas flow writes a new atlas | daily map of phi's mind: point / cluster / promotion counts. drill via `inspect_atlas` |
| `[DOCKET]` | `inject_docket_digest` → PDS `io.zzstoatzz.phi.docket` blob (CID-cached) | when the docket flow writes a new docket | daily promotion candidates: title + `suggested_shape` only. full rationale one `get_record` away |
| `[OWNED FEEDS]` | `inject_owned_feeds` → graze | every run | phi's curated graze feeds, by name |
| `[SEMBLE]` | `inject_public_memory` → `core/public_memory.py` → PDS `network.cosmik.*` reads (5min cache) | every 5min | phi's public library: collection names with card counts, most recent cards, connection count — so saving/filing decisions happen against real state instead of bare counts |

## 3. path-specific blocks (appended to the user message)

assembled by the entry point and appended to the *task prompt*, not the system prompt — so they appear only on their path:

| path | blocks | source |
|---|---|---|
| **notifications** | `[FIRST INTERACTION WITH @h]` per unfamiliar author, + any post images as multimodal inputs | `utils/lookup.py`, pre-fetched by the handler |
| **cycle** | `[WORKFLOW STATE]`, `[RECENT FLOW MENTIONS]`, `[RECENT CONVERSATIONS]` | `core/workflow_state.py`, `core/recent_flow_mentions.py`, `_recent_conversations_block` |
| **daily reflection** | `[RECENT CONVERSATIONS]`, `[SERVICE HEALTH]` | `_recent_conversations_block`, `_check_services_impl` |

because `inject_notifications` / `inject_user_memory` / `inject_episodic` return `""` without a notifications context, the **cycle** and **reflection** paths run with the every-run system blocks above plus their own appended blocks — but no notifications / per-author / episodic blocks.

## design rules

**docstrings, not prompt restatement.** the framework surfaces tool docstrings to the model. per-tool guidance lives in the docstring; the system prompt is for cross-cutting rules. re-describing a tool in the prompt drifts when the tool changes.

**identifiers in the block.** `[KNOWN RELAYS]` puts exact hostnames in the label so phi can't hallucinate. `[GOALS AND INTERESTS]` puts the rkey in the label so `propose_goal_change(rkey=...)` / `update_goal_progress(rkey=...)` target the right record. surface the exact identifier where it'll be used.

**synthesize before injecting where shape matters.** raw top-K from a vector store ranks by cosine similarity — it doesn't reconcile contradictions or note recency. for blocks where the model needs a *coherent* view (recent posts → `[SELF-AWARENESS]`, episodic candidates → `[RELEVANT MEMORIES]`), a small haiku pass produces a block phi can act on directly. per-author observations are *not* synthesized — reconciliation already curated them on write.

**cache canonical reads, not derived ones (separately).** PDS reads (goals, last follow) cache at 5min so 10s-cadence polls don't hammer PDS. haiku passes that depend on phi's posts cache longer (1h) and invalidate on new-post-URI change. PDS blobs (atlas, docket) cache by record CID — they only change when their flow rewrites them.

**empty-when-unset.** dynamic blocks return `""` when their input is absent.

## audit it

the system prompt for any specific run is captured by pydantic-ai's logfire integration. query the `agent run` span where `gen_ai.agent.name = 'phi'` — `attributes.pydantic_ai.all_messages[0]` is the full system message, with each dynamic block as a separate `text` part.
