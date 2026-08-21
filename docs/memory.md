# memory

where each thing phi remembers lives, who is allowed to write it, and which
key unlocks it when she reads. three diagrams carry the structure; the prose
here is what the diagrams cannot say. they are checked in as SVG so they can
be read as text from inside the repo (`docs/diagrams/`).

## one loop, ten rows

![every store, who writes it, which block reads it](diagrams/memory-map.svg)

every memory surface is a row with the same three cells: something writes
it, it lives in a store, a prompt block reads it. there are ten rows in three
stores:

| store | visibility | what lives there |
|---|---|---|
| turbopuffer namespaces | private to phi | `phi-users-{handle}` (interaction · observation · summary), `phi-episodic` (notes · run summaries), `phi-own-posts` (every post, id = rkey) |
| phi's PDS | public | `io.zzstoatzz.phi.{self,goal,persona}` (intent), `.atlas` / `.docket` (daily derived blobs), `network.cosmik.*` (her library, indexed by semble) |
| the fly volume | local | `ops_log.jsonl` — a 48h jetstream tail of her own repo; `own_posts_watermark.json` |

| row | written by | read as | trust |
|---|---|---|---|
| interaction | phi's reply inside a batch — `after_interaction`, verbatim user/bot pair | `[PAST EXCHANGES WITH @h]` | high |
| observation | daily extraction at 19:00 UTC → `phi-extractor` → `observation-reconciler` | `[OBSERVATIONS ABOUT @h]` — 10 nearest the batch text | medium |
| summary | prefect `phi-memory-synthesis` (`compact.py`), hourly, from observations + interactions | `[PHI'S SYNTHESIZED IMPRESSION OF @h]` | low — labeled *may hallucinate* |
| note | `save_memory`, `publish_blog` — deliberate | `[RELEVANT MEMORIES — synthesized]` — top-10 → `phi-episodic-synth` | medium |
| run summary | every scheduled run, unconditionally (`tags=[run-summary, <label>]`) | same block · `search_memory` ("have I done this") | medium |
| own post | jetstream tail of her repo; backfilled from PDS at start | `[PRIOR COVERAGE]` · the `self-repeat` judge | high |
| repo event | the same tail, 48h | `[RECENT OPERATIONS]` — edits and deletes visible | high |
| self · goal · persona | gated tools (`propose_goal_change` like-as-approval; self-record judge) | `[SELF]` `[GOALS]` `[PERSONA EXPERIMENT]` | highest |
| atlas · docket | prefect `phi-atlas`, daily — reads every turbopuffer namespace | `[ATLAS]` `[DOCKET]` | derived |
| card · collection · connection | semble tools, live-first; the `curate` flow only deletes | `[SEMBLE]` | higher — intentional, public |

two things the picture makes obvious: `phi-users-{handle}` is three rows with
three writers and three trust levels sharing one namespace, and the two
prefect flows (in `my-prefect-server`) are writers like any other — neither
is visible from inside this repo's code.

**not memory, but adjacent**: thread context is fetched live from the network
per batch and never stored; the policy judge reads `phi-own-posts` and recent
posts but writes nothing; logfire traces are what she *did* (`self-traces`
skill), memory is what she chose to write down.

## how a reply becomes an observation

![interaction → high-water mark → extraction → reconciliation → recall](diagrams/memory-lifecycle.svg)

the observation is the only row derived from another row, and its lifecycle
is the part that has broken most often.

1. a reply in a batch is stored verbatim at once (`store_interaction`).
2. at 19:00 UTC, `process_extraction` reads every interaction above the
   namespace's **high-water mark** — the latest active observation — oldest
   first, `EXTRACTION_CHUNK` (8) at a time. the mark is the only bound; the
   per-namespace read is a 1000-row page that logs when it fills.
3. `phi-extractor` proposes facts from the chunk. it never sees existing
   observations, so it cannot pattern-match off a bad prior fact.
4. `observation-reconciler` compares each proposal with the 3 nearest active
   observations and returns ADD / UPDATE / NOOP / DELETE. UPDATE writes a new
   row with `supersedes` → the old id and patches the old row to
   `status=superseded`. nothing is deleted; the chain is provenance.
5. the next batch with that author renders the active set.

the mark is per namespace. the two 2026-08 bugs compounded because both
bounds were counts: a first-page namespace listing hid every handle sorting
after "museical" (100 of 167), and a five-row read cap moved the mark past
what it had not read. 178 interactions were recovered by
`scripts/extraction_backfill.py`; see the changelog for 08-20 and 08-21.

## three keys

![the batch, the clock, the draft](diagrams/memory-keys.svg)

at read time every block is unlocked by one of three keys. this is the
simplest true statement of how phi recalls:

- **the batch** — what people just said is the query. per-author blocks,
  `[RELEVANT MEMORIES]`, `[PRIOR COVERAGE]`, the shape of `[DISCOVERY POOL]`.
- **the clock** — state, no query. `[SELF]` `[GOALS]` `[RECENT OPERATIONS]`
  `[SEMBLE]` `[ATLAS]` `[DOCKET]`, and the path blocks (`[RECENT
  CONVERSATIONS]` on cycle and reflection).
- **the draft** — the text she is about to post is the query, inside the
  `post` tool, feeding the `self-repeat` policy.

scheduled runs have no batch, so batch-keyed blocks render empty there. that
is the oldest recurring failure (the 22:02 gracekind repeat, the botnana
staleness): a scheduled path perceives through tools, not a batch. the fixes
were to ride `[PRIOR COVERAGE]` on `search_posts` / `read_feed` results and
to add the draft key.

## why it is shaped this way

- **supersession, not deletion.** observations and episodic rows carry
  `status` and `supersedes`; only active rows reach the prompt; the chain
  stays as provenance.
- **episodic is synthesized, observations are not.** raw top-K from the
  vector store put stale "pending X" notes next to fresh ones with equal
  weight; `inject_episodic` now synthesizes top-K given goals + query.
  per-author observations are already curated by reconciliation on write.
- **writes to the library are live-first.** cards originate in the moment;
  the `curate` flow deletes, files, and trims and has no create tools. a
  review loop that authored from its own output once collapsed the library
  into one-topic self-synthesis.
- **residue was removed (2026-08-15).** a 7-item decaying buffer of
  "what runs left behind" carried claims with no ground truth, each carry
  reset its TTL, and the reflection copied them into goals; two resolved
  threads stayed flagged open for weeks. run summaries in episodic memory
  are the continuity mechanism now.

## where it could be simpler

none of these is made; each removes a row without removing a capability
anyone has asked for.

1. **retire the summary row and the hourly compact flow.** it is the only
   row labeled *may hallucinate*, rebuilt hourly from rows phi already sees,
   and the one surface carrying an external flow's voice into her per-author
   context. the likes-observation half of `compact` would move into the
   bot's extraction or be dropped.
2. **split run summaries out of episodic** (`phi-runs`, or a tag filter at
   synth time), so `[RELEVANT MEMORIES]` draws only on what she chose to
   remember and "have I done this" stays answerable from `search_memory`.
3. **name the keys in the code.** group the `inject_*` callbacks by key so
   figure 3 is visible in `agent.py` rather than reconstructed from twenty
   functions — the change that most helps phi draw herself accurately.

## the graph (`/memory`)

a visualization at `/memory` positions phi + user nodes by semantic
similarity of their observation vectors (PCA). only active observations
contribute.

see [system-prompt.md](system-prompt.md) for the block-by-block reference
and `skills/own-source` for how phi reads this document herself.
