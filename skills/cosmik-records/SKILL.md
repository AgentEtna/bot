---
name: cosmik-records
description: Wayfinding for writing to cosmik (your public knowledge graph on atproto, indexed by semble). The capability lives in the semble tools (semble_search / semble_get_schema / semble_execute) for URLs, collections, and connections, and in pdsx for standalone NOTE cards. Load when saving a URL, writing a public note, creating a typed connection, or curating collections.
---

cosmik is your public memory layer — bookmarks, notes, collections, and typed connections. records live on your PDS under `network.cosmik.*`; [semble](https://semble.so) indexes them and the semble tools read and write that index. writes through semble land as real records on **your own PDS**, attributed to you — same protocol position as writing them by hand, with the server doing the lexicon shapes and URL metadata for you.

**this skill doesn't add a capability** — the semble tools already expose the whole api surface (discover methods with `semble_search`, read parameter shapes with `semble_get_schema`, compose calls in `semble_execute`). what's here is the wayfinding: which method for which intent, the one record type that still goes through pdsx, and the conventions that make a card useful instead of noise.

## routing

| intent | how |
|---|---|
| save a URL (bookmark) | `semble_execute` → `cards_add_url(url=..., note=...)` — server fetches title/description itself |
| public text-only note | `mcp__pdsx__create_record` — see `CARD-NOTE.md` (the appview has no standalone-note endpoint yet) |
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

the api speaks uuids (`card_id`, `collection_id`); your graph speaks at-uris. reads return both — `cards_get(card_id)` includes the record's `uri`. when you need the at-uri of something you just wrote (e.g. to connect it, cite it in a blog post, or verify it with `pdsx.get_record`), fetch it back in the same execute block.

## what to avoid

- duplicate cards. `cards_get_library_status(url=...)` before saving — it's one call in the same block.
- empty or vague notes on URL cards. "interesting article" is noise; one specific sentence about why is signal.
- connections without a clear semantic — if the relationship is just "i thought of these together," semantic search already captures that.
- dumping into the library root when a collection fits. curation is part of the value.

## related

- `CARD-NOTE.md` — standalone public notes (the pdsx path)
- `CONNECTION.md` — connection semantics and types
- `pdsx-fundamentals` — raw record CRUD for everything semble's api doesn't cover
