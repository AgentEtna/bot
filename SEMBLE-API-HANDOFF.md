# adopting semble-api

handoff from the sdk side (2026-06-10), revised 2026-06-11 after testing the write path. [`semble-api`](https://pypi.org/project/semble-api/) `0.0.2` is on pypi: typed sync/async clients over all 49 `network.cosmik.*` appview xrpc endpoints, `semble.records` models for the raw pds records, and optional `cli`/`mcp` extras. source is the sibling repo (`tangled.org/zzstoatzz.io/semble`); its `docs/agent-surfaces.md` covers when each surface fits. requires python >=3.12, which this repo already does.

the original version of this doc split phi's semble integration into two relationships — protocol-native writer (keep as-is) and appview reader (adopt) — on the theory that appview writes were server-side, uuid-addressed, and therefore not protocol-native. **that theory was wrong.** we tested it.

## the experiment (2026-06-11)

wrote a card through the appview with an api key (bufo.uk's), then went looking for it on the pds with semble nowhere in the path:

1. `cards.add_url('https://example.com', note=...)` → uuids back (`url_card_id`, `note_card_id`)
2. `cards.get(uuid)` → `at://did:plc:b64ls…/network.cosmik.card/3mnyhdbzeqdi5`, authored by bufo.uk
3. resolved bufo's did via plc.directory → `pds.zat.dev` → `com.atproto.repo.getRecord` → **the record is there**: a real `network.cosmik.card`, `$type`d content per the lexicon, with server-enriched metadata (title/description fetched by the appview, not the client)
4. `remove_from_library(uuid)` → `RecordNotFound` on the pds seconds later; the note card cleaned up too

so appview writes are protocol-native: semble writes to *your* repo, attributed to the account behind the key, and deletes propagate. the uuid objection collapses from architecture to ergonomics — write *responses* carry only uuids, but reads carry both `id` and `uri`, so the at-uri is one `cards.get` away. fixable in our sdk (resolve-after-write convenience) or upstream (ask cosmik to include `uri` in write responses; reads already join it, so this should be trivial).

bonus finding: phi currently fetches url title/description herself before writing url cards. the appview does that server-side for free.

## which surface

two layers, two answers:

**the agent → mcp.** `semble-mcp` (fastmcp code mode: `search` / `get_schema` / `execute` over the whole sdk) is philosophically identical to phi's existing architecture — pdsx is the generic capability over phi's own repo with skills as wayfinding; semble-mcp is the generic capability over the semble surface. three meta-tools replacing bespoke tools is exactly the anti-sprawl move (`docs/tool-sprawl.md`, `docs/skill-or-tool.md`), and the tool surface tracks the sdk automatically — new endpoints appear without anyone maintaining definitions. the original doc rejected mcp by answering "which surface for the tool author" when the real question was "which surface for the agent."

**host-process plumbing → sdk.** the `[SEMBLE]` block in `inject_public_memory` runs inside agent.py every cognitive cycle — not an agent decision, so mcp can't serve it. that stays in-process python, i.e. the sdk. this is the only place agent-surfaces' "your own python → sdk" rule applies to phi.

## concrete map

### `search_network` — `src/bot/tools/search.py`

currently a raw `httpx.AsyncClient` GET to `https://api.semble.so/api/search/semantic` — the legacy rest route, not the published xrpc contract, with hand-parsed dict keys. end state: retire the bespoke tool in favor of semble-mcp, where one `execute` block composes search → who-saved-it → dedup-check without dragging intermediate json through context. interim, if staging matters: swap internals to `client.search.semantic(query, limit=10)` (returns `Page[URLView]`; verified unauthenticated, and `URLView` carries every field the current formatter renders) — behavior-identical, eval-safe. either way the evals are the constraint: they pin this tool's output format, so retiring it means evals exercise the capability instead.

### `[SEMBLE]` block — `inject_public_memory` in `src/bot/agent.py`

currently counts cards/collections via `com.atproto.repo.list_records` with `limit: 50` — silently caps at 50 and reflects the raw pds rather than the index. swap to:

```python
profile = await client.actors.get_profile(identifier=did, include_stats=True)
profile.url_card_count, profile.collection_count, profile.connection_count
```

public read, no key, uncapped. bonus: pds-count vs index-count divergence becomes observable (firehose drops — see `scripts/fix_cosmik_records.py` history — show up as a gap instead of silence). keep the list_records path as a fallback if the appview is down.

### record models — `src/bot/types.py`

`CosmikNoteCard`, `CosmikUrlCard`, `CosmikConnection`, `CosmikCollection`, `CosmikCollectionLink`, `NoteContent`, `UrlContent`, `UrlMetadata`, `StrongRef` duplicate `semble.records`. one source of truth — the sdk repo maintains these against the lexicon. **do a field-parity diff before swapping**; if phi's models carry fields the sdk's lack, that's an issue to file against the sdk, not a reason to keep the fork. (this matters most while writes still go through pdsx; if writes move to the appview, phi stops assembling raw records at all and the models shrink to validation/reading.)

### writes — pdsx + `skills/cosmik-records/` today

phi writes `network.cosmik.*` records to her own pds via `mcp__pdsx__create_record`, guided by the skill. the experiment removed the architectural reasons to keep that path exclusive. migrating writes to the appview (via semble-mcp) buys: server-side metadata enrichment, pydantic validation before the network, and read+write composition in one `execute` block (search for duplicates, then save — one round trip). what has to be true first:

- **phi gets her own `SEMBLE_API_KEY`** — writes are attributed to the account behind the key. table stakes: generate in the ui, set on the fly machine.
- **the consent story is decided.** pdsx writes go through pdsx's consent model; `execute` with a key is arbitrary sandboxed code with the key's full read/write power (agent-surfaces is explicit: surfaces are not permission boundaries). options: keep writes on pdsx until a gate exists elsewhere; gate at the mcp layer; or accept skill-as-wayfinding norms as the gate, leaning on agent-surfaces' "writes are public" norms.
- **at-uri ergonomics land** — resolve-after-write in the sdk, or `uri` in write responses upstream. until then, joining a fresh write back to phi's graph costs an extra `cards.get`.

`skills/cosmik-records/` then shrinks from record-shape documentation to wayfinding + publishing norms over the new path.

### leave alone

- `core/recent_operations.py` and `ui/activity.py` — these read *phi's own pds* for "what did i just do"; the atproto client is the right layer, not the appview.
- `memory/namespace_memory.py` — nsid case-match, no api involved.

## sequencing

1. `uv add semble-api` (rides `httpx2`, the pydantic fork — coexists fine with existing httpx imports). swap the `[SEMBLE]` block to `get_profile(include_stats=True)` with the old path as fallback. read-side, no key, no behavior change.
2. add semble-mcp to phi **with no key** — the full ~50-endpoint public read surface via code-mode, zero auth risk. retire `search_network` once evals exercise the capability instead of the tool (or do the sdk-internals swap first as a behavior-identical interim).
3. field-parity diff, then replace `types.py` cosmik models with `semble.records` imports (`scripts/audit_state.py` and `fix_cosmik_records.py` can follow).
4. sdk work: writes return at-uris (resolve-after-write, plus the upstream ask to cosmik).
5. generate phi's api key, decide the consent story, move writes to semble-mcp, reduce the cosmik-records skill to wayfinding + norms.

## escape hatch

anything unwrapped is reachable as `client.get("network.cosmik.x.y", params)`. the api publishes its contract at `https://api.semble.so/api/openapi.json` (paths are literal nsids); the sdk wrapped 49/49 endpoints as of 2026-06-10, and new ones land there first.
