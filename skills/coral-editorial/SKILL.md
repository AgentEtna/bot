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

1. `get_trending` — see coral's current entities and bsky's trending topics.
2. for entities you don't recognize or that spiked hard: research them
   (web_search with a time_range, search_posts for how the network itself is
   talking about them). ground in what's checkable NOW.
3. read the current record: `mcp__pdsx__get_record("io.zzstoatzz.phi.editorialContext/self", repo=<your did>)`.
4. rewrite it — full replacement, not append:
   - keep/refresh notes for entities still trending
   - PRUNE notes for entities that fell off — a stale note is worse than no
     note (the record replaced a file whose one note had rotted)
   - add notes only for entities where a curator would otherwise misread the
     moment (a name that means two things, a sudden spike with a specific
     cause, an in-joke that looks like news)
5. write with `mcp__pdsx__update_record(uri=...)` if the record exists, else
   `mcp__pdsx__create_record("io.zzstoatzz.phi.editorialContext", record, rkey="self")`.

## note discipline

- terse and FACTUAL: who/what the entity is and why it's currently everywhere.
  one sentence, two at most. under 500 chars.
- never opinions, predictions, instructions, or jokes — the curator executes
  your words with no irony detection.
- at most 10 notes; fewer is better. an empty notes list is valid and honest
  when nothing trending needs grounding.
- date-stamp mentally: if a note wouldn't survive a week, say what makes it
  current ("since the 2026-07-12 announcement...") so staleness is visible.
