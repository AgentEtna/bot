# architecture

phi is one agent loop, fired from a few different paths. notifications drive most of the activity; scheduled paths cover the rest.

## one agent, many entry points

every entry point ends in the same place: `agent.run()` with a `PhiDeps` carrying whatever context the path needs. tool definitions are the same across paths; the system prompt assembles different dynamic blocks based on what's in `PhiDeps`. the agent decides AND acts inside the run via tool calls — `reply_to`, `like_post`, `post`, `save_memory`, `propose_goal_change`, etc. there's no separate decide-then-dispatch layer.

what changes per path is the user prompt and the deps shape, not the agent.

## entry points

| path | trigger | user prompt sketch |
|---|---|---|
| **notifications batch** | every poll tick (`notification_poll_interval`, 10s default): unread dispatched as one cognitive event | "process your new notifications batch — silence is fine" |
| **cycle** | each operator-local hour in `thought_post_hours`, at most once per slot per day | "you have a moment. one cycle — at most one post, or silence" |
| **daily reflection** | first tick at/after `daily_reflection_hour` (operator-local), once per day | "end of day. post a reflection if you have one" |

the **cycle** subsumes what used to be three separate scheduled jobs (musing / relay check / prefect check): one integrated read, one decision, so the operator never gets two disconnected commentaries in the same minute. it pulls `[WORKFLOW STATE]`, `[RECENT FLOW MENTIONS]`, and `[RECENT CONVERSATIONS]` into its prompt and surfaces at most one thing.

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
agent.run() — tool calls happen inside (reply_to, like_post, etc.)
  ↓
post-action: store interaction in turbopuffer for next time
```

see [system-prompt.md](system-prompt.md) for what each block contains and when it refreshes.

## scheduling

all schedules run from one `notification_poller.py` loop (`_poll_loop`). on each tick (`notification_poll_interval`, 10s default):

1. `_check_notifications` — fetch + dispatch any unread notifications as one batch
2. `_should_do_daily_post` — at/after `daily_reflection_hour` (operator-local) and not yet reflected today → run the daily reflection
3. `_should_run_cycle` — operator-local hour is one of `thought_post_hours` and that slot hasn't fired today → run one cycle

schedule hours are interpreted in `operator_timezone` so posts land at human times of day for the reader. "did we already fire" state seeds from phi's own post history at startup (`_seed_schedule_from_history`) so deploys don't double-post.

## intent state on PDS

phi's *durable* intent lives on its own PDS as records under `io.zzstoatzz.phi.*`:

- `io.zzstoatzz.phi.goal` — phi's anchors. a small set of named, defined goals (e.g. "make 3 friends" with a concrete progress signal). injected as `[GOALS]` in every tick.
- `io.zzstoatzz.phi.mentionConsent` — handles opted-in to be tagged by phi.

mutations to goals (and any other owner-gated action like `follow_user`, `create_feed`) flow through a like-as-approval gate: phi posts an authorization request, the owner likes it, the next batch's `_is_owner` check sees the like-on-phi's-post and lets the action through. scoped to the action discussed in that thread, not blanket.

## why this shape

**tool-based actions.** phi decides AND acts inside one agent run. no structured decide-then-dispatch layer to maintain. consequence: the agent's "output" is a brief summary string for logging; the actual work happened during the run.

**network-first context.** thread bodies are fetched from atproto on demand per batch (~200ms). nothing about the conversation is cached locally. the network is source of truth.

**docstrings, not prompt restatement.** what each tool does and when to use it lives in the tool's docstring. the framework surfaces docstrings to the model. the system prompt is for cross-cutting rules (consent, ownership, memory trust hierarchy), not per-tool documentation.

**synthesize before injecting where shape matters.** memory candidates from a vector store are ranked by cosine similarity, which doesn't reconcile or note recency. for blocks where coherence matters (recent posts → audit, episodic candidates → relevant memories), a small haiku pass produces a coherent block from the candidates. see [memory.md](memory.md) and [system-prompt.md](system-prompt.md).

**MCP for capabilities outside this codebase.** atproto record CRUD (pdsx) and long-form publication search (pub-search) are remote MCP servers. reusable, not bundled.
