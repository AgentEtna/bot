---
name: cosmik-records
description: Wayfinding for writing to cosmik (your public knowledge graph on atproto via semble). The capability — writing any network.cosmik.* record — already lives in pdsx; this skill is the per-record-type schema details and conventions so you do it well. Load when saving a URL, writing a public note, or creating a typed connection.
---

cosmik is your public memory layer — bookmarks, notes, and typed connections, indexed by [semble](https://semble.so) and discoverable via `search_network`. records live on your PDS under `network.cosmik.*`. this skill covers the three you'll actually write.

**this skill doesn't add a capability** — pdsx already lets you write any cosmik record. you could call `mcp__pdsx__create_record(collection="network.cosmik.card", record={...})` without ever loading this skill; you'd just have to figure out the schema and conventions yourself. what's here is the wayfinding: the right shape per record type, when to reach for which, and the conventions that make a card actually useful instead of noise.

read `pdsx-fundamentals` first if you haven't — this skill assumes you understand `mcp__pdsx__create_record` and the consent layer. cosmik writes are **not** owner-gated; you can write notes/cards/connections freely. they're public, but they're yours.

## what's in this namespace

| record type | purpose | resource file |
|---|---|---|
| `network.cosmik.card` (NOTE kind) | a public note — text-only, like a tweet that lives on your PDS instead of bsky | `CARD-NOTE.md` |
| `network.cosmik.card` (URL kind) | a bookmark — a URL with title/description metadata | `CARD-URL.md` |
| `network.cosmik.connection` | a typed link between two cards (e.g. SUPPORTS, OPPOSES) | `CONNECTION.md` |

cards and connections together form a directed graph. semble indexes both.

## when to use which

- **NOTE** when the value is the *text*. a thought you want public but doesn't fit a bsky post. a synthesis of something you've been thinking about. an observation you want to be discoverable later by search rather than scrolling.
- **URL** when the value is *what someone else made*. an article, a paper, a thread, a leaflet doc. include a short description in your own words for why it's worth reading.
- **CONNECTION** when two cards relate in a way that's worth marking explicitly — "this paper supports that argument," "this URL contradicts that earlier note." use after creating both endpoints; you need their AT-URIs.

a heuristic: if you'd want to post it on bsky, post it on bsky. if you want it on a record someone could find via semantic search later, save it as a card.

## minimum example: writing a NOTE

```
mcp__pdsx__create_record(
  collection="network.cosmik.card",
  record={
    "kind": "NOTE",
    "content": {"text": "..."}
  }
)
```

returns `{"uri": "at://did:plc:.../network.cosmik.card/3xxxxx", "cid": "..."}`. semble's firehose subscriber picks it up automatically; no explicit indexing call needed.

for full schemas and richer examples, read the per-record-type resource files (`CARD-NOTE.md`, `CARD-URL.md`, `CONNECTION.md`).

## what to avoid

- duplicate cards. before saving a URL, search semble (`search_network`) to see if it's already indexed.
- empty or vague descriptions on URL cards. "interesting article" is noise; one specific sentence about why is signal.
- connections without a clear semantic — if the relationship is just "i thought of these together," that's weaker than the cards' co-occurrence in semantic search will already capture.

## related

- `pdsx-fundamentals` — the underlying CRUD mechanics
- `search_network` (registered tool) — query semble for what's already there
