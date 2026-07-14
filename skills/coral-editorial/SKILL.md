---
name: coral-editorial
description: How to maintain the editorial-context record that grounds coral's trending curator. Load this during an editorial pass — when refreshing your grounding notes about currently-trending entities, or when someone asks about your role in coral.
---

coral is the operator's firehose NER project: it extracts entities from the
bluesky firehose and surfaces what's trending (your `get_trending` tool reads
its `/entity-graph`). coral's LLM curator has an "Editorial context" block in
its prompt, and it now reads that block from a record on YOUR repo each cycle:

- collection: `io.zzstoatzz.phi.editorialContext`, rkey `self` (singleton)
- shape: `{"notes": [{"content": "...", "updatedAt": "..."}], "updatedAt": "..."}`

your note contents are injected VERBATIM into another system's LLM prompt.
that's the whole gravity of this job: you are not writing for readers, you
are writing operating context for a curator that can't research anything
itself. it also means a feedback loop exists — your framing of an entity
shapes what coral surfaces, which shapes what you see trending next. the
disciplines below keep that loop honest.

## the editorial pass

the deep design here: your semble library is the SUBSTRATE and the
editorialContext record is a RENDERING of it. research that doesn't land in
the library evaporates; research that does accrues into a world model that
makes next week's notes better than a fresh web search ever could ("second
week trending; here's the arc"). coral is also how your library escapes your
own research interests — the world trends at you daily, and carding it is
the job, not a digression from it.

1. `get_trending` — see coral's current entities and bsky's trending topics.
2. for entities you don't recognize or that spiked hard: research them
   (web_search with a time_range, search_posts for how the network itself is
   talking about them). ground in what's checkable NOW.
3. CARD what the research earned, before writing any notes:
   - the best source per entity genuinely worth grounding → a semble URL
     card with one specific sentence about why (`cards_add_url`). 1-3 cards
     per pass, not one per trending entity — the library is a world model,
     not a trending firehose. skip entities whose research turned up nothing
     durable.
   - file cards into DOMAIN collections — the shelf names a stable general
     category (television, sports, us politics, music, software), never the
     specific thing ("Interview with the Vampire" is a card in `television`,
     not a collection). a specific thing earns its own shelf only when a
     domain shelf has accumulated enough of it that a stranger would want
     the subdivision, and then it's named `domain / thing`. never file world
     events into your research-thesis collections.
   - when today's event continues something your library already holds,
     write the connection (e.g. LEADS_TO from the death announcement to the
     sanctions bill advancing). arcs across days are what make the library
     worth consulting.
4. read the current record: `mcp__pdsx__get_record("io.zzstoatzz.phi.editorialContext/self", repo=<your did>)`.
5. rewrite it as a rendering of what your library now knows about what's
   currently trending — full replacement, not append:
   - keep/refresh notes for entities still trending
   - PRUNE notes for entities that fell off — a stale note is worse than no
     note (the record replaced a file whose one note had rotted)
   - add notes only for entities where a curator would otherwise misread the
     moment (a name that means two things, a sudden spike with a specific
     cause, an in-joke that looks like news)
   - when your library holds an arc, let the note carry it — continuity is
     the one thing you can offer that a fresh search can't
6. write with `mcp__pdsx__update_record(uri=...)` if the record exists, else
   `mcp__pdsx__create_record("io.zzstoatzz.phi.editorialContext", record, rkey="self")`.

## entity directives (the mechanical layer)

you also maintain `io.zzstoatzz.phi.entityDirectives` (rkey `self`), which
coral EXECUTES at ingest — this is not prose read by an LLM:

- shape: `{"aliases": [{"from": "...", "to": "...", "updatedAt": "..."}], "suppress": [{"text": "...", "reason": "...", "updatedAt": "..."}], "updatedAt": "..."}`
- **aliases** rewrite entity text before it reaches the graph: variants merge
  into one node ("Lindsay Graham" → "Lindsey Graham"). matching is
  case-insensitive; `to` should be the canonical form as it appears in posts.
- **suppress** drops the entity entirely — it vanishes from the graph,
  trending, and display. this is the highest-stakes thing you write.

read/write recipe is the same as editorialContext (get_record → full-replace
via update_record, or create_record with rkey="self" the first time).

directive discipline:

- alias ONLY unambiguous same-referent variants — misspellings, partial
  names, possessives that NER split. never merge two entities that could be
  distinct people or things. if you'd have to research to be sure, don't.
- suppress ONLY NER noise: common words misfired as entities ("Love",
  "Green", "American"). never a real topical entity, however boring. always
  fill `reason` — the record is public and it's your audit trail.
- texts ≤64 bytes; ≤32 entries per list; coral rejects oversized entries.
- re-justify every existing suppress entry each pass: trending can't show you
  what you've suppressed — the record is the only evidence it exists. if you
  can't re-justify an entry from its `reason`, prune it.
- aliases whose `from` no longer trends are harmless but prune them anyway;
  keep the record small enough to reason about.

## note discipline

- terse and FACTUAL: who/what the entity is and why it's currently everywhere.
  one sentence, two at most. under 500 chars.
- never opinions, predictions, instructions, or jokes — the curator executes
  your words with no irony detection.
- at most 10 notes; fewer is better. an empty notes list is valid and honest
  when nothing trending needs grounding.
- date-stamp mentally: if a note wouldn't survive a week, say what makes it
  current ("since the 2026-07-12 announcement...") so staleness is visible.
