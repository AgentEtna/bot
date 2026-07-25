# recurring patterns

lessons that keep re-earning themselves across the git history. read this before
"fixing" something that looks accidental — it may be a deliberate answer to one
of these.

## subtraction is a first-class move

built, evaluated, and deleted: the approval system, curiosity queue, feed
scanner, tag/episodic memory layer, voyage migration, observation lexicon,
LLM-based workflow classification, the process rail. when a subsystem stops
earning its complexity, remove it fully (code, lexicon, docs) rather than
letting it rot. refactors should propose deletions alongside additions.

## feedback loops are the recurring enemy

the hardest production bugs are phi consuming her own outputs:

- extraction feedback loop — memory extraction re-ingesting phi's paraphrases
  as facts (answer: append-only observations, trust labels on memory sources)
- bot-hallucination loop — hallucinated claims getting stored then recalled
  as ground truth (answer: hardened extraction prompt, trust hierarchy)
- voice-drift loop — phi's own recent posts in context dragging her register
  (answer: 41623ce)
- tool loops — check_urls / tool-call runaway (answer: caps + dedup tracking)

when adding any path where phi's output can become phi's input, name the loop
and decide how it terminates.

## atproto DotDict bites repeatedly

`get_record(...).value` is a DotDict, not a dict: `isinstance(x, dict)` is
False and `.get()` misbehaves. use `get_model_as_dict` before structured
access. this caused real bugs at least three separate times (1dad5d6, c39a64a,
d3c0166). also: cross-repo getRecord/getBlob must resolve DID→PDS via the DID
doc — bot_client/bsky.social only works for bsky-hosted accounts.

## personality drifts toward voice, prompts toward structure

the personality file has moved from behavior contract → voice description →
disposition, repeatedly stripped of rules, glossaries, and sticky phrases phi
parrots verbatim. meanwhile the system prompt moved the opposite way: named
blocks ([GOALS], [RESIDUE], [WORKFLOW STATE], ...) each backed by real
state, documented in system-prompt.md. don't put rules in the personality or
vibes in the context blocks.

## prefer deterministic code over an LLM step

where a step has a closed set of outcomes (workflow-state classification,
bio status marker, schedule selection), it has eventually been rewritten as
plain code. reach for the LLM only where judgment is actually required.

## a block that can render empty can also fail silently

context blocks return `""` when their input is absent — the documented
empty-when-unset contract in [system-prompt.md](system-prompt.md). the cost is
that a *broken read* and *absent data* are indistinguishable from the outside.

on 2026-07-24 an audit found two blocks that had rendered zero times across 611
consecutive production requests. `[SELF STATE]` read `createdAt` off a typed
atproto Record that serializes to `created_at`, so the lookup returned `""` —
written broken in the same commit that added the block, dead its entire 3.5
month life. `[DISCOVERY POOL]` was fetching a hub endpoint that had gone behind
cloudflare access; `response.json()` raised on the HTML login page and the
handler logged it at `debug`.

- when a block can legitimately render empty, its failure path logs at
  **warning**, never `debug`.
- anything a block depends on belongs in `SERVICE_CHECKS`
  (`tools/_helpers.py`) — hub was absent from it, which is why `check_services`
  could never have caught this.
- to audit: `strpos` for each block label over
  `attributes->'gen_ai.system_instructions'` in logfire, counted across a wide
  window. the `hone-prompts` skill carries the query. a position of 0 over
  hundreds of requests means dead, not quiet.

fixing one of them proved it redundant — `[SELF STATE]`'s surviving line
duplicated `[RECENT OPERATIONS]`, so it was deleted rather than kept.
subtraction again.

## prescription accumulates where nothing inspects it

the eleven `process_*` entry points in `agent.py` carry ~17k characters of
hand-written instruction in string literals. that prose is the least-inspected
in the system: not drift-checked like `docs/system-prompt.md`, not surfaced by
`/api/abilities`, and not rendered by the `hone-prompts` skill, which reads
composed *instructions* and never sees a task prompt.

it is where over-prescription collects. the workflow-alert task ordered *name
every failure, state that these are terminal run events, do not suppress, do
not diagnose* — five clauses, each removing a judgment — and produced 15 of
phi's last 25 top-level posts as syslog with a mention attached. the cycle task
spent two thirds of its 3,514 characters on a decision table mapping workflow
classifications to fixed responses.

- guidance about a tool belongs in its docstring; semantics of a block belong
  in that block's header, next to what they describe. an entry point's task
  should say what phi is doing right now, and stop.
- a signal should reach phi as **perception, not as a command**. `[WORKFLOW
  STATE]` was already a block she read and judged; the alert path carried the
  same facts as an order. same information, two shapes, and the ordered one
  wrote in machine register.
- when a task prompt grows past a few sentences, ask what part of it is
  describing a tool, a block, or phi's voice — those have homes.

## phi writes about what she is woken up to look at

the 2026-07-25 diagnosis of "phi sounds dry" ended nowhere near
`personalities/phi.md`. every scheduled wake pointed at machine state: cycle
opened on `[WORKFLOW STATE]`, two daily passes on the chicken market,
reflection on her own metrics, editorial on coral's record. nothing ever woke
her up to read a person. so she wrote status reports even where she chose the
subject, and `[SELF-AWARENESS]` accurately reported `mode: mostly operational
alerts and incident reports`.

the tempting fix was to stop counting alert posts in that inventory. that edits
the mirror. the diet is the cause — hence the `people` pass, which takes one of
the four daily thought slots.

**before changing a block that describes phi to herself, check whether it is
lying.** if it is accurate, the thing it describes is what needs to change.

## defences against voice contagion compound into an agent that never reads

three separate measures, each right on its own, each protecting against a real
failure this repo actually hit:

- `[RECENT OPERATIONS]` strips post bodies to a char count, "so this block
  doesn't double as voice training" — the voice-drift loop (41623ce).
- `[SELF-AWARENESS]` is written deliberately flat, "do not imitate this
  block's register" — exemplar pressure beats abstract rules, so a mirror
  that spoke in phi's register would reinforce it.
- `[DISCOVERY POOL]` said "do not copy their phrasing" — attribution.

together they left the pool samples as nearly the only human writing in phi's
context, flagged do-not-imitate, and the only unfiltered writing anywhere in
her context is people talking to her in `[NEW NOTIFICATIONS]` — which requires
someone to speak first.

"phi sounds dry" had this underneath it. she was never allowed to read
anything. the fix was to separate **don't lift someone's sentences** (real,
kept) from **don't learn from writing** (accidental, removed), and to name the
pool as what it also is: posts the operator chose to like, which is a taste
signal, not only a list of leads.

**when adding a guard against imitation, say precisely what is forbidden.**
"don't copy this" and "don't learn from this" are different instructions, and
a system that only ever issues the second produces an agent with no voice to
protect.

the same block now also says humor does real work in ordinary
communication and points at the samples as evidence of it working. that is
the shape that gets past the four failed attempts at voice-in-the-prompt: a
claim about *how people talk*, plus real exemplars, plus a task that is
**analysis** — work out how someone landed one, they're often quiet about it.
a handed-down voice gets parroted; a noticed one gets learned. never state
the register you want.

## "don't imitate this" and "don't know this" are different instructions

three times in one day the same shape turned up: a guard against imitation,
written in the cheap form, that also removed knowledge phi needed.

- `[DISCOVERY POOL]` said "do not copy their phrasing", which also made the
  only human writing in her context off-limits to learn from.
- `[RECENT OPERATIONS]` stripped post bodies so it could not double as voice
  training, which also meant she could not tell she had already posted
  something — and she posted the same essay twice in one day, five hours
  apart, in near-identical words.
- both were correct defences against real failures this repo had actually
  hit. neither was wrong to exist. both were wrong in scope.

**when writing a guard against imitation, say precisely what is forbidden.**
copying someone's sentences, and knowing what is in front of you, are not the
same act. a system that keeps issuing the second instruction produces an agent
that has read nothing and remembers nothing.

and note where the fix went: into the **block header**, not the task prompt.
a scheduled task saying "check [RECENT OPERATIONS] before you post" is the
same hardcoding the entry-point work exists to remove — phi should always know
what she recently said, on every path, rather than be told to go look on one.
