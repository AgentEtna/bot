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

`note` was renamed → `remember` so the recall/remember pair (read verb,
write verb) is coherent. `remember` writes to turbopuffer `phi-episodic`
(private vector) — saved for future semantic recall, never re-surfaces
on its own, queryable via `recall`.

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
(list existing first, remember after) so the skill and the tool
reinforce each other.

`list_blog_posts` is a candidate for deletion (read-only, pdsx covers
it via `list_records(collection="app.greengale.document")`) but
`publish_blog_post` calls it internally and the `publish-blog` skill
recommends it for "what should i write about next." marginal benefit;
**leaving for now**.

## what was done this round

1. **renamed `note` → `remember`.** the recall/remember pair makes the
   read-vs-write distinction obvious and disambiguates from `observe`.
2. **excluded `run_skill_script` from the SkillsToolset.** every skill
   we ship is documentation-only (markdown + resource files); leaving
   the script-execution tool registered was extra capability surface
   phi never used. one fewer tool, free.
3. **enriched `publish-blog` SKILL.md** with the before/after procedure
   (list existing → publish via tool → optional remember pointer) and
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
| private memory | `remember`, `recall` | turbopuffer is not exposed as an MCP; pdsx can't reach it |
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
