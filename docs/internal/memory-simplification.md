# memory simplification plan (internal)

> **internal** — a plan, dated 2026-08-21. none of it is built. each section
> names the cut, what it removes, what must survive, the files, the tests,
> and what would make us stop. order is the recommended build order.

context: [../memory.md](../memory.md) and its three diagrams. the loop is
ten rows; this plan removes one row, regroups the readers, and adds one
exhaust the loop does not have yet.

## 0. the judge's exhaust (add first — it measures the rest)

**what exists.** `check_action` runs the policy judge on every `post` and
governed reaction, returns `{verdict, policy?, reason?}` to phi as the tool
result, and `logger.warning`s only the non-allow cases. The judge's own
model call lands in logfire as a `chat` span (prompt + output), so the
reasoning *exists* — but only in logfire, only findable by hand, and
`reason` is omitted on allow.

**what to add.** a judge ledger: one line per verdict, append-only, on the
fly volume next to the ops log.

```
/data/judge_ledger.jsonl
{ts, tool, verdict, policy, reason, action_sha, action_head (120 chars),
 provenance_kind (invited|unprompted|operator-authorized),
 evidence: {recent_posts: n, prior_coverage: n}, latency_ms, model}
```

- `reason` becomes required for every verdict (one sentence on allow too —
  "no policy applies" is a fine sentence). the output schema already has
  the field; the prompt stops saying "omit when allow".
- `action_sha` lets a retry after a block be linked to its block: the pair
  (blocked draft, allowed redraft) is the single most useful artifact the
  system can produce about its own judgment.

**who reads it.**

1. phi, in `[SELF]`: a seven-day tally folded into the posting inventory —
   `judge: 41 allow · 3 warn (bliss-attractor ×2, self-repeat) · 1 block
   (self-repeat)`. a trend she can see is a norm she can hold; today the
   only feedback is the tool result, forgotten by the next run.
2. the operator, in the cockpit: `/judge` — the ledger as a table,
   filterable by policy/verdict, each row expandable to the block→redraft
   pair when one exists. this is where "is the judge right" gets answered.
3. the policies themselves, later: a week of block/redraft pairs is the
   evidence for editing `POLICIES`. (no automation proposed; a person reads
   the pairs.)

**files.** `core/policy.py` (emit), new `core/judge_ledger.py` (append,
read window, tally), `core/self_state.py` (fold the tally into the
inventory block), `main.py` (`/api/judge`), `web/` (`/judge`).
**tests.** ledger append/rotate; tally rendering; block→redraft pairing by
`action_sha` within one run; `test_docs_sync` row for the inventory change.
**stop if** the ledger line ever carries the full action text — it holds a
head and a hash, the full text is in logfire.

## 1. retire the `summary` row and the hourly compact flow

**what it removes.** `kind = summary` rows in `phi-users-{h}`, the
`[PHI'S SYNTHESIZED IMPRESSION OF @h — trust: low, may contain
hallucinations]` block, and `flows/compact.py`'s synthesis half
(`synthesize_summary`, `write_summary_to_turbopuffer`) in
my-prefect-server.

**why.** it is the only row labeled *may hallucinate*; it is rebuilt hourly
from rows phi already sees (observations are reconciled, exchanges are
verbatim); it is the one surface that carries an external flow's voice into
her per-author context; and it costs a haiku call per active person per
hour.

**what must survive.**

- the likes half of `compact` (`extract_likes_observations`: observations
  from posts phi liked). it is real signal about people she did not talk
  to. move it into the bot's own `process_extraction` as a second source
  (liked posts newer than the mark, same extractor, same reconciler) — or
  keep it as a trimmed flow `phi-likes-observations` with no synthesis. the
  first is the cleaner end state; the second is the smaller diff.
- the cockpit's `/api/users/{handle}` shows the summary; it shows the
  active observation set instead.

**files.** bot: `memory/namespace_memory.py` (`get_relationship_summary`,
`build_user_context` summary branch, `USER_NAMESPACE_SCHEMA` keeps the kind
for old rows), `main.py:412-540`, `docs/memory.md`, `docs/system-prompt.md`.
prefect: `flows/compact.py`, `prefect.yaml` (deployment
`phi-memory-synthesis` and its transform trigger), `tests/test_compact_retries.py`.
**tests.** `build_user_context` renders no impression block; likes
observations still reach the reconciler; the docs-sync test for the removed
block.
**migration.** leave existing summary rows in place (append-only); nothing
reads them. a one-off count before and after for the changelog.
**stop if** someone is actually using the impression for first-contact
texture that observations do not give. check: the block's trust label has
said *may hallucinate* since it shipped, and phi's own retro has never
cited it.

## 2. group the `inject_*` callbacks by key

**what it changes.** no behavior. `agent.py` registers ~20 `@system_prompt`
callbacks in one flat sequence. group them under three small registrars
that mirror figure 3:

```
_register_clock_blocks(agent)   # identity, now, self, goals, recent ops, semble, atlas, docket, relays, feeds
_register_batch_blocks(agent)   # notifications, per-author memory, episodic, prior coverage, discovery pool
# the draft key lives in tools/posting.py already
```

**why.** the structure phi reads (`docs/memory.md`) and the structure she
runs (`agent.py`) should have the same shape; today the keys are
reconstructed by the reader from twenty functions. it also makes the
empty-on-scheduled-path behavior obvious at the registration site instead
of inside each callback.

**files.** `agent.py` only; `docs/system-prompt.md` gains a "key" column.
**tests.** `test_docs_sync` unchanged (function names unchanged); a new
assertion that every `inject_*` is registered by exactly one registrar.
**stop if** the regroup wants to reorder blocks — order is part of the
prompt cache key, and reordering resets the cache. keep registration order
identical; the grouping is lexical.

## 3. split run summaries out of episodic

**what it changes.** `store_episodic_memory(..., source="run:<label>")`
writes to a `phi-runs` namespace instead of `phi-episodic`. `inject_episodic`
synthesizes notes only; `search_memory` searches both and labels which.

**why.** two writers share one store; the synth has to dedupe a log against
notes; the residue incident showed what a synthesized log does when it
corroborates itself. "have I done this" stays answerable because
`search_memory` still reaches the runs.

**files.** `memory/namespace_memory.py` (`NAMESPACES`, `store_episodic_memory`
routing, `search_unified`), `agent.py` run-summary write, `docs/memory.md`.
**tests.** a run summary lands in `phi-runs`; `[RELEVANT MEMORIES]` never
contains a `run-summary` tag; `search_memory` returns both kinds labeled.
**migration.** backfill existing `run-summary` rows into `phi-runs`
(`tags` already marks them); leave the originals superseded, not deleted.
**stop if** phi's "have I done this" recall measurably drops — the
episodic-synth block was where that recall surfaced without her asking.
watch the next week of `cycle finished` summaries for re-discovery.

## order and size

0 first (it instruments the others), 2 second (no behavior change, makes
1 and 3 easier to see), then 1, then 3. relative size: 2 < 3 < 0 < 1, with
1 the only one that touches two repos.
