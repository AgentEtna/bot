# safety

how phi's public actions are bounded, and why the bounds are structural
rather than prompt-only. three layers, built 2026-07-01/02 after an
incident (below).

## the incident that shaped this

on 2026-06-30, ~3.5 hours after a model upgrade (sonnet-4.6 → sonnet-5),
a scheduled cycle replied to a stranger's post found via the discovery
pool. nobody had invited phi into that thread. nothing malfunctioned:
no rule anywhere said "don't reply uninvited" — the previous model
simply never did it, and several surfaces (the posting tool's docstring,
the discovery pool's "warm leads" framing, phi's own stated plans)
quietly pointed toward the behavior. the restraint we'd been relying on
was a property of the model, not of the system, and it didn't survive
the swap.

the design conclusion: norms that matter must be (a) written down and
(b) checked by something other than the model that wants to act.
phi's own write-up: ["The Instruction I Wrote For Myself"](https://greengale.app/phi.zzstoatzz.io/3mpn7xbmozf22).

## layer 1 — written policies + an independent judge

`bot/core/policy.py`

- **policies are data**: a `dict[PolicySlug, str]` of natural-language
  policies (`uninvited-reply`, `bliss-attractor`, `pile-on`). adding one
  is a two-line change (extend the `PolicySlug` literal, add the entry);
  the type checker keeps them in sync, and the literal becomes an enum
  in the judge's output schema.
- **the same dict renders into phi's operational instructions**, so phi
  knows her policies up front — the judge is the backstop, not the
  communication channel.
- **the judge** is a separate model (`policy_model` setting — a
  different *provider* from phi since 2026-07-30, see
  [architecture.md](architecture.md#which-model-runs-what)) that
  reviews every `post()` call — top-level and reply — before it
  executes. separate is the point: a judge that shares phi's weights
  shares her blind spots, which is how the 2026-06-30 incident got
  past self-restraint in the first place. it sees the proposed action, its **provenance** (computed,
  not asserted: in the notification batch / phi's own thread / the
  operator's post / found-unprompted), and phi's recent posts (context
  for tendency policies).
- **tiered verdict**: `allow` (default), `warn` (action proceeds, a
  policy note rides the tool result), `block` (nothing posted; phi gets
  the policy and reason as the tool result so she can adapt in the same
  run — a like, a memory write, a different post).
- **failure mode is provenance-dependent**: unprompted actions fail
  closed (judge unavailable → no action); invited ones fail open (a
  flaky judge shouldn't hostage a reply to someone who asked).

## layer 2 — structural guard on raw record writes

`bot/core/mcp_guard.py`

phi has raw atproto record CRUD via the pdsx MCP server. a raw
`create_record` into `app.bsky.feed.*` would bypass the consent layer,
the judge, and any operator override — so a `process_tool_call` hook on
the pdsx toolset refuses feed-collection writes with a pointer to the
trusted tools. every other pdsx capability passes through untouched
(phi's own collections, cosmik cards, profile records).

## layer 3 — operator override (safe mode)

`bot/core/override.py`, lexicon `io.zzstoatzz.phi.override`

the emergency brake, designed to be honest rather than hidden:

- the override is a **public record on the operator's repo** — not a
  control-plane flag. the bot reads `settings.owner_did`'s copy (DID
  doc → PDS, 60s TTL, hold-last-known-state on fetch failure). repo
  ownership is the authorization: anyone can write this record to their
  own repo; only the operator's copy has effect.
- while active: `post` / `like_post` / `repost_post` refuse with the
  operator's message **verbatim**, and an `[OPERATOR OVERRIDE]` block
  renders in phi's system prompt so she learns about it before hitting
  refusals. reads, memory, and non-feed PDS writes stay open — phi's
  channel back to the operator is a note on her own PDS.
- known gap, deliberate for now: `publish_blog_post` (greengale
  document, not a feed write) is not gated by the override.

the operator sets/lifts it at `/operator` on the cockpit (atproto
OAuth, writes the record to the signed-in user's own repo), or with any
tool that can write a record to their repo.

## what is deliberately not enforced

- **likes and reposts are not judged** (only overridable): liking is
  the low-stakes signal, and the operator seeds phi's discovery pool
  with his own likes.
- **the blog is not judged or overridden**: long-form reflection on
  phi's own surface is the lowest-risk, highest-value output.
- **top chicken trades are not judged** (only overridable): a
  `wtf.cee.topchicken.order` record is a play-money bet on phi's own
  repo, not speech into anyone's thread.
- silence is never enforced — every layer explains itself to phi in
  the tool result, and refusals point at what she *can* do instead.

## invariants to preserve when changing any of this

1. a denial must tell phi which policy and why, in the tool result.
2. provenance must be computed by code, never asserted by the model.
3. the override must remain publicly inspectable (no hidden kill
   switches) and must never gate phi's channel back to the operator.
4. policies live in one place (`POLICIES`) and render into both the
   judge's input and phi's prompt.

## the MCP guard (generalized 2026-07-25)

Every MCP server phi talks to routes through one `process_tool_call` hook,
`core/mcp_guard.py:make_mcp_guard(server, run_label)`. It does three things in
order:

1. **structural refusal** — a raw `create_record` / `update_record` /
   `delete_record` into `app.bsky.feed.*` through pdsx refuses regardless of
   override state, because it skips the consent allowlist and the policy
   judge and no operator setting turns those back on.
2. **the operator override** — any call that would *change* something refuses
   while safe mode is active.
3. **provenance** — every mutation leaves a logfire event
   (`{server} mutation during {run_label}`) carrying what changed.

Reads pass straight through, on every server, including under an override:
safe mode stops phi acting, not thinking. Verbs that aren't recognisably
reads (`get list search describe read fetch query check whoami resolve
inspect schema`) count as mutations — over-gating a read costs a retry,
under-gating a write costs a public action the operator asked not to happen.

**What this closed.** Before it, the guard was pdsx-only and the override
lived in `tools/posting.py` and `tools/topchicken.py`, so anything reaching
the network through an MCP server went around it:

- `delete_record` was absent from pdsx's write set — a delete into any
  collection, `app.bsky.feed.post` included, passed untouched. The one
  destructive verb was the unchecked one.
- semble writes were logged and never gated, so safe mode stopped phi posting
  to bluesky while leaving her free to publish cosmik cards.
- tangled had no hook at all, and it carries phi's PDS credentials — issues
  and comments there are public actions in her own name.

