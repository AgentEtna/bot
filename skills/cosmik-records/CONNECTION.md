# network.cosmik.connection

a typed directed link between two entities (cards or raw URLs). semble renders these as edges on the public knowledge graph.

## schema

required fields:

- `source` — string. either an AT-URI (`at://did:plc:.../network.cosmik.card/3xxx`) pointing at one of your cards, OR a raw URL (`https://example.com/...`) for an external endpoint.
- `target` — string, same shape as `source`.

optional fields:

- `connectionType` — string. the semantic type of the link. unset means "associated, no claim about how."
- `note` — string, max 1000 chars. context for the connection if the type alone isn't enough.

`createdAt` / `updatedAt` are auto-injected by the write helper. you don't pass them.

## connection types worth using

- `SUPPORTS` — `source` provides evidence or argument for `target`
- `OPPOSES` — `source` argues against or undermines `target`
- `ADDRESSES` — `source` answers, responds to, or directly handles `target`
- `HELPFUL` — `source` is useful for working with or understanding `target`
- `EXPLAINER` — `source` explains `target`
- `LEADS_TO` — `source` points toward `target` as a next step or consequence
- `SUPPLEMENTS` — `source` adds supporting context without being primary evidence
- `RELATED` — weaker, generic association — use sparingly

## minimum example

```
mcp__pdsx__create_record(
  collection="network.cosmik.connection",
  record={
    "source": "at://did:plc:.../network.cosmik.card/3aaa",
    "target": "at://did:plc:.../network.cosmik.card/3bbb",
    "connectionType": "SUPPORTS"
  }
)
```

source and target can be heterogeneous — one card and one external URL is fine, two external URLs is also fine (though usually it's worth at least one being a card you've made).

## when not to make a connection

semble's vector search already surfaces semantically-related cards together. a connection is worth writing when the relationship is *specific and directional* — not just "these are about the same thing."

if you find yourself reaching for `RELATED` constantly, that's a sign the cards are already adjacent in semantic space and the connection isn't doing real work.

## related

- `CARD-NOTE.md` — endpoint type 1
- `CARD-URL.md` — endpoint type 2
