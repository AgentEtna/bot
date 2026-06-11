# connections

a typed directed link between two entities (cards or raw URLs). semble renders these as edges on the public knowledge graph.

## how to write one

`semble_execute` → `connections_create`. the endpoint addresses each end by type + value:

```python
result = await call_tool("connections_create", {
    "source_type": "URL",            # "URL" or "CARD"
    "source_value": "https://example.com/paper",
    "target_type": "CARD",           # CARD takes the card's id (uuid), not its at-uri
    "target_value": "<card_id>",
    "connection_type": "SUPPORTS",
    "note": "optional context if the type alone isn't enough",
})
```

`source_type`/`source_value`/`target_type`/`target_value` are required; `connection_type` unset means "associated, no claim about how." card ids come from `cards_list_mine`, `cards_get_library_status`, or the result of a `cards_add_url` in the same block.

## connection types worth using

- `SUPPORTS` — source provides evidence or argument for target
- `OPPOSES` — source argues against or undermines target
- `ADDRESSES` — source answers, responds to, or directly handles target
- `HELPFUL` — source is useful for working with or understanding target
- `EXPLAINER` — source explains target
- `LEADS_TO` — source points toward target as a next step or consequence
- `SUPPLEMENT` — source adds supporting context without being primary evidence
- `RELATED` — weaker, generic association — use sparingly

(these are the api's exact enum values — `semble_get_schema(tools=["connections_create"])` is the source of truth if this list ever drifts.)

## when not to make a connection

semble's vector search already surfaces semantically-related cards together. a connection is worth writing when the relationship is *specific and directional* — not just "these are about the same thing."

if you find yourself reaching for `RELATED` constantly, that's a sign the cards are already adjacent in semantic space and the connection isn't doing real work.
