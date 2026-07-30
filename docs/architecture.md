# architecture

phi is one agent loop, fired from a few different paths. notifications drive most of the activity; scheduled paths cover the rest.

## one agent, many entry points

every entry point ends in the same place: `agent.run()` with a `PhiDeps` carrying whatever context the path needs. tool definitions are the same across paths; the system prompt assembles different dynamic blocks based on what's in `PhiDeps`. the agent decides AND acts inside the run via tool calls — `post`, `like_post`, `save_memory`, `propose_goal_change`, etc. there's no separate decide-then-dispatch layer.

what changes per path is the user prompt and the deps shape, not the agent.

## which model runs what

three settings, all full pydantic-ai `provider:model` strings:

| setting | agents | model |
| --- | --- | --- |
| `agent_model` | `phi`, `phi-extractor` | `anthropic:claude-sonnet-5` |
| `policy_model` | `phi-policy-judge` | `openai-responses:gpt-5.6-luna` |
| `extraction_model` | `phi-episodic-synth`, `observation-reconciler`, `phi-posting-inventory`, `phi-residue-synth` | `openai-responses:gpt-5.6-luna` |

phi herself stays on one model deliberately. her voice is the product of a specific model reading [personalities/phi.md](../personalities/phi.md), and `core/cache_stability.py` wraps `agent_model` only — the cache accounting reads Anthropic's `cache_read_tokens` / `cache_write_tokens` off each response, so it observes the main agent and nothing else. sub-agents produce *structured context*, not voice, which is why they can move independently.

**the `openai-responses:` prefix is load-bearing.** every sub-agent above has an `output_type`, which pydantic-ai sends as a function tool, and OpenAI reasoning models reject function tools on `/v1/chat/completions` when `reasoning_effort` is set. the chat-completions path fails with a 400 on every call, not intermittently. a new sub-agent pointed at an OpenAI reasoning model needs the same prefix.

the settings carry the provider because two call sites used to prepend it themselves (`f"anthropic:{...}"`) while two others didn't — harmless while everything ran on one provider, silently wrong at half the sites otherwise. `tests/test_config.py::TestSubAgentModelStrings` is the guard.

## entry points

| path | trigger | user prompt sketch |
|---|---|---|
| **notifications batch** | every poll tick (`notification_poll_interval`, 10s default): unread dispatched as one cognitive event | "process your new notifications batch — silence is fine" |
| **workflow failure alert** | each newly observed Prefect Failed/Crashed run ID (`workflow_failure_poll_interval`, 60s default) | "alert the operator about these exact terminal events" |
| **cycle** | each operator-local hour in `thought_post_hours` that is *not* in `people_pass_hours`, at most once per slot per day | "you have a moment. what have you been thinking about?" |
| **people** | the `people_pass_hours` subset of those same slots (default 17:00 local) | "this one is about people, not systems" — phi picks narrow (one person worth reading) or wide (a question about a group) and knows why |
| **daily reflection** | first tick at/after `daily_reflection_hour` (operator-local), once per day | "end of day. post a reflection if you have one" |

the **cycle** subsumes what used to be three separate scheduled jobs (musing / relay check / prefect check): one integrated read, one decision, so the operator never gets two disconnected commentaries in the same minute. it pulls `[WORKFLOW STATE]`, `[RECENT FLOW MENTIONS]`, and `[RECENT CONVERSATIONS]` into its prompt and surfaces at most one thing.

it used to open on `[GOALS AND INTERESTS]`, then list `[WORKFLOW STATE]` first among what to look at, then spend two thirds of its length on a decision table mapping each workflow classification to a fixed response. every scheduled wake pointed at machine state, so phi wrote status reports even when she picked the subject — `[SELF-AWARENESS]` reported `mode: mostly operational alerts and incident reports`, accurately. the cycle now opens on what she has been thinking about and names infrastructure as one of the things she can see rather than the point of looking; the label semantics moved into the `[WORKFLOW STATE]` block header, next to the labels they define. the **people** pass exists because nothing in the schedule ever sent her to read a person.

## data flow (notifications)

```
bsky.notification.listNotifications (every 10s)
  ↓
filter unread × allow-list (rate limit per author)
  ↓
build notifications_context: per-notif fetch (post body, thread context,
  reply refs, embeds), pre-fetch stranger profiles for unfamiliar authors
  ↓
PhiDeps assembled, system prompt composed:
  identity / time / known relays / goals / self-awareness / self state
  / notifications block / per-author memory / synthesized episodic / ...
  ↓
agent.run() — tool calls happen inside (post, like_post, etc.)
  ↓
post-action: store interaction in turbopuffer for next time
```

see [system-prompt.md](system-prompt.md) for what each block contains and when it refreshes.

## scheduling

all schedules run from one `notification_poller.py` loop (`_poll_loop`). on each tick (`notification_poll_interval`, 10s default):

1. `_check_notifications` — fetch + dispatch any unread notifications as one batch
2. `_should_do_daily_post` — at/after `daily_reflection_hour` (operator-local) and not yet reflected today → run the daily reflection
3. `_should_run_cycle` — operator-local hour is one of `thought_post_hours` and that slot hasn't fired today → run one cycle, or the people pass when the hour is in `people_pass_hours`
4. `_check_workflow_failures` — poll Prefect once a minute and dispatch each new Failed/Crashed run ID once

schedule hours are interpreted in `operator_timezone` so posts land at human times of day for the reader. "did we already fire" state seeds from phi's own post history at startup (`_seed_schedule_from_history`) so deploys don't double-post.

workflow failure delivery is deliberately separate from the cycle's latest-state
classification. a flow may fail and recover between cycle slots; the event monitor
still delivers the failed run once. delivered run IDs persist in `/data/status.json`
so deploys do not replay old incidents. the first monitor poll seeds existing history
without announcing it.

## intent state on PDS

phi's *durable* intent lives on its own PDS as records under `io.zzstoatzz.phi.*`:

- `io.zzstoatzz.phi.goal` — phi's goals and interests. each carries constitutional fields (title / description / progress_signal / kind, owner-gated via `propose_goal_change`) and operational fields (current_state / next_step / last_step / blocked_by, phi-writable via `update_goal_progress`). injected as `[GOALS AND INTERESTS]` in every tick, with a "stalled" line that gives an untouched goal visible pressure.
- `io.zzstoatzz.phi.mentionConsent` — handles opted-in to be tagged by phi.

mutations to goals (and any other owner-gated action like `follow_user`, `create_feed`) flow through a like-as-approval gate: phi posts an authorization request, the owner likes it, the next batch's `_is_owner` check sees the like-on-phi's-post and lets the action through. scoped to the action discussed in that thread, not blanket.

## why this shape

**tool-based actions.** phi decides AND acts inside one agent run. no structured decide-then-dispatch layer to maintain. consequence: the agent's "output" is a brief summary string for logging; the actual work happened during the run.

**network-first context.** thread bodies are fetched from atproto on demand per batch (~200ms). nothing about the conversation is cached locally. the network is source of truth.

**docstrings, not prompt restatement.** what each tool does and when to use it lives in the tool's docstring. the framework surfaces docstrings to the model. the system prompt is for cross-cutting rules (consent, ownership, memory trust hierarchy), not per-tool documentation.

**synthesize before injecting where shape matters.** memory candidates from a vector store are ranked by cosine similarity, which doesn't reconcile or note recency. for blocks where coherence matters (recent posts → audit, episodic candidates → relevant memories), a small sub-agent pass produces a coherent block from the candidates. see [memory.md](memory.md) and [system-prompt.md](system-prompt.md).

**MCP for capabilities outside this codebase.** atproto record CRUD (pdsx) and long-form publication search (pub-search) are remote MCP servers. reusable, not bundled.
