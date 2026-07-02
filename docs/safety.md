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
- **the judge** is a separate model (`policy_model` setting) that
  reviews every `post()` call — top-level and reply — before it
  executes. it sees the proposed action, its **provenance** (computed,
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
- silence is never enforced — every layer explains itself to phi in
  the tool result, and refusals point at what she *can* do instead.

## invariants to preserve when changing any of this

1. a denial must tell phi which policy and why, in the tool result.
2. provenance must be computed by code, never asserted by the model.
3. the override must remain publicly inspectable (no hidden kill
   switches) and must never gate phi's channel back to the operator.
4. policies live in one place (`POLICIES`) and render into both the
   judge's input and phi's prompt.
