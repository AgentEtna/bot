# tool sprawl

surfacing the agent's tool catalogue in the UI made the sprawl visible. the
cockpit's `tools` lens currently lists ~30 tools across 10 source modules and
the categorization is incoherent. flagging this so it can be addressed at
the source — the UI just renders what's there.

## inventory (current)

| module | tools |
|---|---|
| `tools/memory.py` | `search_memory`, `save_memory` |
| `tools/posting.py` | `post` (top-level or reply via `in_reply_to`), `like_post`, `repost_post` |
| `tools/search.py` | `search_posts`, `web_search`, `get_trending` |
| `tools/bluesky.py` | `get_own_posts`, `check_urls`, `manage_labels`, `manage_mentionable`, `check_services`, `check_relays`, `changelog` |
| `tools/feeds.py` | `create_feed`, `list_feeds`, `delete_feed`, `read_timeline`, `read_feed`, `follow_user` |
| `tools/goals.py` | `list_goals`, `propose_goal_change`, `update_goal_progress` |
| `tools/blog.py` | `list_blog_posts`, `publish_blog_post` |
| `tools/atlas.py` | `inspect_atlas` |
| `tools/bio.py` | `write_bio` |
| `tools/media.py` | `inspect_record_media` |

**removed**: `tools/cosmik.py` (`save_url`, `create_connection`) — these are now
covered by the `cosmik-records` skill. phi loads it on demand and uses
`mcp__pdsx__create_record` to write `network.cosmik.*` records of the right
shape, instead of going through per-record-type tool wrappers.

**removed (2026-06-11)**: `search_network` — replaced by the semble code-mode
MCP toolset (`semble_search` / `semble_get_schema` / `semble_execute`), which
carries the whole semble api (reads *and* writes) as three meta-tools. same
philosophy as the cosmik.py deletion, one level up: a generic capability plus
the `cosmik-records` skill as wayfinding, instead of one bespoke tool per
operation. URL cards, collections, and connections now write through it;
standalone NOTE cards remain on pdsx (no appview endpoint for those).

## concrete misplacements that jump out

1. ~~**`post` lives in `bluesky.py` but `reply_to` / `like_post` / `repost_post` live in `posting.py`.**~~ resolved: `post` and `reply_to` collapsed into one `post(text, in_reply_to="")` tool in `posting.py`. top-level vs reply is one call; the URI restriction moved from prompt-rule ("must be in [NEW NOTIFICATIONS]") to tool-level verify-by-fetch, so phi can thread her own posts naturally.
2. **`follow_user` is in `feeds.py`.** following is a graph operation, not a feed operation. it has nothing to do with the graze-feeds cluster (`create_feed` / `list_feeds` / `delete_feed` / `read_feed` / `read_timeline`). it should move.
3. ~~**`note`, `save_url`, `create_connection` are all "create a cosmik record"**~~ — resolved: cosmik write tools deleted (replaced by cosmik-records skill via pdsx). `note` was actually a private-memory write to turbopuffer, not a cosmik write — that misclassification was the smell. the private-memory write tool is now `save_memory` (see "the naming smell" in [skill-or-tool.md](skill-or-tool.md)).
4. ~~**`memory.py` has just `recall` + `note`.**~~ resolved: the pair is `search_memory` (read) + `save_memory` (write) — each verb names its operation. (history: write `note` → `remember` → `save_memory`; read `recall` → `search_memory`; see "the naming smell" in [skill-or-tool.md](skill-or-tool.md).)
5. **`manage_labels` and `manage_mentionable` are in `bluesky.py`** but they're operator-only self-management of phi's identity boundaries — they belong with `goals` / `observations` (other operator-gated identity stuff) or in their own `self.py`.
6. **`check_urls` is in `bluesky.py`.** it's a generic URL HEAD request — nothing bluesky about it.
7. **`check_services`, `check_relays`, `changelog` are scattered across `bluesky.py`** but they're a coherent monitoring cluster — distinct from posting.
8. **`feeds.py` mixes graze CRUD (`create_feed`, `delete_feed`) with reading (`read_timeline`, `read_feed`, `list_feeds`).** different lifecycles, probably worth splitting.
9. **`list_blog_posts` is a registered agent tool AND `blog.py` is also where the `publish-blog` skill body lives.** skill vs tool overlap on the same surface area is confusing.

## scale

30 native tools is a lot. each adds JSON-schema + docstring to every prompt phi
runs. some still want consolidation. one resolved case: `post` + `reply_to`
collapsed into one `post(text, in_reply_to="")` — threading falls out of
the parameter, not a separate tool, and the URI restriction moved from a
prompt rule ("must be in [NEW NOTIFICATIONS]") to a tool-level
verify-by-fetch so phi can thread her own posts. `like_post` /
`repost_post` / `follow_user` stay as distinct verbs since they're
inherently subject-referencing actions, a different shape from
record-create-with-optional-parent.

`inspect_record_media` is intentionally narrow: pdsx remains the generic
record CRUD/discovery surface, while this tool only turns allowed text/image
blobs on an already-known AT-URI into model-readable text or image content.

## what the UI actually wants from the bot

the cockpit currently hand-syncs `web/src/lib/abilities.ts` with what's in
the source. that drifts the moment a tool is added/renamed/moved. proposed
backend endpoint:

```
GET /api/abilities

[
  {
    "name": "search_memory",
    "module": "memory",
    "doc": "search private memory for past conversations or things i know about people.",
    "operator_only": false,
    "category": "..." // if you decide on real categories upstream, expose them here
  },
  ...
]
```

this lets the UI show ground truth (real names, real docstrings, real
operator-gated flag) instead of hand-curated copy. i (the UI side) was
inventing category names + first-person verb framings that have no basis
in the source — that won't happen again, but the structural fix is
exposing this metadata properly so there's nothing to invent.

## skills

`pydantic-ai-skills` are a different thing from agent tools (load-on-demand
SKILL.md packs vs. always-available `@agent.tool` functions). they were
mixed into the same UI surface because i conflated them; that's been
removed. with only one skill (`publish-blog`) there's no UI surface for
them right now. when there are more, the cleanest cockpit integration is
probably as a kind on the `mind` lens — skills as objects phi can pull
into attention, alongside observations/goals — rather than a separate
lens. flagging for when the catalogue grows.

## suggested order of operations

1. expose `/api/abilities` (introspect the agent's registered tools, grab
   docstrings, expose `module` + `operator_only`). this unblocks the UI
   from hand-curation.
2. consolidate the obvious misplacements above (8 specific moves).
3. ~~consider consolidation of fine-grained engagement tools into one
   `engage(kind, ...)` tool.~~ resolved differently: `post` + `reply_to`
   merged into one `post(text, in_reply_to="")` (threading via the
   parameter), and verify-by-fetch at the tool layer replaces the prompt
   rule. `like_post` / `repost_post` / `follow_user` stay distinct.
4. resolve the `blog.py` tool/skill overlap.

step 1 alone makes the UI honest. steps 2–4 reduce the surface area phi
has to think about every prompt.
