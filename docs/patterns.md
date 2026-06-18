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
blocks ([SELF STATE], [GOALS], [WORKFLOW STATE], ...) each backed by real
state, documented in system-prompt.md. don't put rules in the personality or
vibes in the context blocks.

## prefer deterministic code over an LLM step

where a step has a closed set of outcomes (workflow-state classification,
bio status marker, schedule selection), it has eventually been rewritten as
plain code. reach for the LLM only where judgment is actually required.
