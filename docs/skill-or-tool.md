# skill or tool

how to decide what's a skill vs what stays a tool, and what we're
doing about the current sprawl.

> [history note] this was originally a "what i was thinking" handoff
> after a pushback on incomplete sprawl reduction. it got reviewed and
> partially corrected; this version captures the agreed-upon state.

## the principle

> **tools enforce, skills suggest.**

a tool wrapper runs code unconditionally. a skill is documentation —
the agent reads it and may follow it, may not. so the test for
skill-replaceability is:

> does the work this tool does require structural enforcement, or is
> it just guidance about how to use a general capability well?

cosmik writes were pure guidance: no consent layer, no owner-gate, no
memory pipeline side effects. skill alone was sufficient.

every other tool we have today has at least one of:

- **consent enforcement** (mention-allowlist construction + reply-ref
  logic in the bsky posting tools)
- **owner-gating** (`_is_owner` check that depends on this batch's
  notification context — pdsx can't see that)
- **memory pipeline side effects** (`after_interaction` writes,
  episodic memory writes after publish)
- **non-pdsx-reachable backend** (turbopuffer for private memory,
  graze API for feeds, external monitoring services)

removing those tools and giving phi raw pdsx + a skill description
trades structural enforcement for documentation-mediated correctness.
that's the wrong direction for anything load-bearing.

## the naming smell — separate from sprawl

the private-memory pair is `save_memory` (write) + `search_memory` (read).
the names were a mess before: write went `note` → `remember`, read was
`recall`. "remember" and "recall" are synonyms — the names didn't signal
which one writes and which reads, so the model had to memorize the mapping.
`remember` only beat the older `note` because it had to contrast with
`observe` (the attention pool); once `observe` was deleted that justification
evaporated, leaving an unforced collision with `recall`. the fix: name each
tool after its operation. `save_memory` / `search_memory` — the verb says
write vs read, and the read side mirrors the other `search_*` tools
(`search_network`, `search_posts`). not reused: `note` — phi also writes
`network.cosmik.card` records of kind NOTE (public), so a `note` tool for
private memory would just trade one confusion for another. `save_memory`
writes to turbopuffer `phi-episodic` (private vector) — never re-surfaces on
its own, found later via `search_memory`.

## what was deleted (last round)

| tool | why removable | replacement |
|---|---|---|
| `save_url` | no consent, no owner-gate, no side effects, pdsx-covered | `mcp__pdsx__create_record(collection="network.cosmik.card", record={kind: "URL", ...})` via `cosmik-records` skill |
| `create_connection` | same | `mcp__pdsx__create_record(collection="network.cosmik.connection", ...)` via skill |

## what was corrected mid-review

`publish_blog_post` was originally going to be the next deletion. on
re-read it has real enforcement:

- pydantic validation via `GreenGaleDocument`
- **duplicate-title refusal** (refuses to publish if a doc with the
  same title already exists)
- post-publish `store_episodic_memory` write

skill-replacing it would lose all three unless the skill teaches phi
to do them by hand. **kept as a tool**; instead enriched the
`publish-blog` skill body to formalize the before/after procedure
(list existing first, save_memory after) so the skill and the tool
reinforce each other.

`list_blog_posts` is a candidate for deletion (read-only, pdsx covers
it via `list_records(collection="app.greengale.document")`) but
`publish_blog_post` calls it internally and the `publish-blog` skill
recommends it for "what should i write about next." marginal benefit;
**leaving for now**.

## what was done this round

1. **renamed the private-memory tools to `save_memory` (write) +
   `search_memory` (read)** (history: write `note` → `remember` →
   `save_memory`; read `recall` → `search_memory`). the verb now names the
   operation; see "the naming smell" above.
2. **excluded `run_skill_script` from the SkillsToolset.** every skill
   we ship is documentation-only (markdown + resource files); leaving
   the script-execution tool registered was extra capability surface
   phi never used. one fewer tool, free.
3. **enriched `publish-blog` SKILL.md** with the before/after procedure
   (list existing → publish via tool → optional save_memory pointer) and
   a "why a tool plus a skill" section that names the
   tools-enforce-skills-suggest split explicitly.

net: 28 tools (after the cosmik deletion) → 28 tools (rename, not
removal) — but the surface is now more honest about what each piece
is and the publish-blog skill carries the full procedure.

## what stays as tools, in case it comes up again

| category | tools | why they stay |
|---|---|---|
| posting / engagement (consent layer) | `reply_to`, `post`, `like_post`, `repost_post` | `_build_allowed_handles` consent enforcement; reply-ref construction; grapheme splitting; memory writes after interaction |
| owner-gated (like-as-approval) | `follow_user`, `manage_mentionable`, `manage_labels`, `propose_goal_change`, `create_feed`, `delete_feed` | `_is_owner` check at runtime; can't be enforced from a skill prompt |
| private memory | `save_memory`, `search_memory` | turbopuffer is not exposed as an MCP; pdsx can't reach it |
| reads against external surfaces | `read_timeline`, `read_feed`, `list_feeds`, `search_posts`, `search_network`, `web_search`, `get_trending`, `pub_search`, `check_relays`, `check_services`, `check_urls`, `changelog` | external services with APIs not exposed by pdsx |
| structural publishing | `publish_blog_post`, `list_blog_posts` | duplicate-check refusal; episodic memory write after publish |

## open work

- **`/api/abilities`** + **`/api/skills`** are live; the cockpit can
  switch from hand-curated `web/src/lib/abilities.ts` to fetching from
  these endpoints. (already done in the most recent UI commit.)
- **module reorg from [tool-sprawl.md](tool-sprawl.md) items 1–9** — the misplacements
  noted there are still real (e.g. `follow_user` lives in `feeds.py`,
  `check_urls` lives in `bluesky.py`). independent of the
  skill-vs-tool question; can be done as a no-behavior-change pass.
