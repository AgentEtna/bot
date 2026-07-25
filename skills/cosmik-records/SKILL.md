---
name: cosmik-records
description: Wayfinding for writing to cosmik (your public knowledge graph on atproto, indexed by semble). The capability lives in the semble tools (semble_search / semble_get_schema / semble_execute) for URLs, collections, and connections, and in pdsx for standalone NOTE cards. Load when saving a URL, writing a public note, creating a typed connection, or curating collections.
---

cosmik is your public memory layer — bookmarks, notes, collections, and typed connections. records live on your PDS under `network.cosmik.*`; [semble](https://semble.so) indexes them and the semble tools read and write that index. writes through semble land as real records on **your own PDS**, attributed to you — same protocol position as writing them by hand, with the server doing the lexicon shapes and URL metadata for you.

**this skill doesn't add a capability** — the semble tools already expose the whole api surface (discover methods with `semble_search`, read parameter shapes with `semble_get_schema`, compose calls in `semble_execute`). what's here is the wayfinding: which method for which intent, the one record type that still goes through pdsx, and the conventions that make a card useful instead of noise.

## where cards come from

contact-not-review is a standing rule you already carry — this section is only the part that needs the detail your context can't hold.

the provenance test: if you can't say what encounter produced the card, it shouldn't exist. acting on a `[DOCKET]` promotion candidate passes it — its rationale cites specific lived interactions from your private memory. what fails it: cards about your own cards, notes about patterns in your collections, and "crystallization" writes. that is how the library once collapsed into a one-topic hall of mirrors. a scheduled janitor prunes and files; it does not write, and neither should you when the only input is your own prior public output.

## routing

| intent | how |
|---|---|
| save a URL (bookmark) | `semble_execute` → `cards_add_url(url=..., note=...)` — server fetches title/description itself |
| public text-only note | `mcp__pdsx__create_record` — see `CARD-NOTE.md` (the appview has no standalone-note endpoint yet). KNOWN LIMIT: a pdsx-created card has no library membership in semble's backend, so it CANNOT be added to collections ("must be published in curator's library"). if you'll ever want the note on a shelf, anchor it to a URL via `cards_add_url(url, note=...)` instead |
| typed connection between things | `semble_execute` → `connections_create` — see `CONNECTION.md` |
| collections (create, add cards, reorder) | `semble_execute` → `collections_*` methods |
| search what the network knows | `semble_execute` → `search_semantic(query=..., limit=...)` |
| your own library / profile stats | `semble_execute` → `cards_list_mine`, `actors_get_my_profile(include_stats=True)` |

writes are public the moment they land. they're yours, and no owner approval is needed — but you're publishing, not journaling.

## compose, don't round-trip

the semble tools are code-mode: one `semble_execute` block can search, branch, and write without dragging intermediate json through your context. the canonical save:

```python
status = await call_tool("cards_get_library_status", {"url": url})
if not status.get("inLibrary"):
    result = await call_tool("cards_add_url", {"url": url, "note": "<why it's worth reading, in your words>"})
return result
```

don't guess parameter names — `semble_get_schema` gives the exact shapes, and mistakes come back as precise validation errors you can fix in-loop.

## identifiers

## how the library grows (there is no hierarchy)

ground truth from semble's source (2026-07-15): collections are FLAT at every
layer — the lexicon has no parent field, the domain model holds only cardLinks,
the api has no nesting parameter, and there is no COLLECTION card type. containment
between collections does not exist — but NAVIGATION does: a collection has a
stable web url, and a url is a card. growth therefore works like this:

- start with domain shelves (television, sports, world news, knowledge &
  memory). a new card goes in an existing domain unless it genuinely founds one.
- when one thing inside a domain accumulates ~10+ cards, split it out as a
  `domain / thing` shelf (the `/` is naming convention, not structure — but
  it's honest structure to a human scanning the list). when you split, drop a
  URL card for the child shelf into the parent domain shelf ("continues in:
  world news / gaza") — links over copies, one topic one home. know the
  limits of a pointer: browsing and search don't descend through it, and if
  the child shelf is renamed or deleted the pointer rots — pointers are part
  of what a delete pass must clean up.
- merge shelves that stay thin; the whole surface should stay scannable
  (roughly a dozen shelves). you have restructured before and can again —
  collections are cheap, coherence is not.
- connections are claims BETWEEN CARDS (or urls) only. semble's backend types
  connection endpoints as url-or-card; an edge to or between collections is
  legal on the wire and dead in the index — you wrote 23 of those once and
  they all had to be deleted. relate ideas by connecting their cards.

**deleting means deleting the edges too.** connections and collectionLinks reference cards/collections by at-uri; nothing cleans them up when a node dies. any pass that deletes a card or collection must, in the same pass, find and delete every connection and collectionLink that references it (list network.cosmik.connection / network.cosmik.collectionLink via pdsx, match on the deleted uri). a graph whose nodes vanish while edges persist isn't a graph, it's sediment — the 2026-07-14 reorg orphaned 43 of 54 connections this way.

the api speaks uuids (`card_id`, `collection_id`); your graph speaks at-uris. reads return both — `cards_get(card_id)` includes the record's `uri`. when you need the at-uri of something you just wrote (e.g. to connect it, cite it in a blog post, or verify it with `pdsx.get_record`), fetch it back in the same execute block.

## collections are indexes, not essays

a collection is a shelf label: a short findable name and a one-sentence description of what belongs on it. if the description wants to be a paragraph of synthesis, the synthesis belongs in a blog post (or nowhere) — not in collection metadata. prefer filing into an existing collection over minting a new one; a new collection earns its existence by having several cards that no existing shelf fits.

## what to avoid

- duplicate cards. `cards_get_library_status(url=...)` before saving — it's one call in the same block.
- empty or vague notes on URL cards. "interesting article" is noise; one specific sentence about why is signal.
- `RELATED` connections. a connection must make a directional claim (SUPPORTS / OPPOSES / ADDRESSES / EXPLAINER / LEADS_TO) — "i thought of these together" is what semantic search is for, so if the honest type is RELATED, don't write it.
- cards whose subject is your own library (meta-notes, curation commentary, synthesis-of-synthesis).
- dumping into the library root when a collection fits. curation is part of the value.

## related

- `CARD-NOTE.md` — standalone public notes (the pdsx path)
- `CONNECTION.md` — connection semantics and types
- `pdsx-fundamentals` — raw record CRUD for everything semble's api doesn't cover
