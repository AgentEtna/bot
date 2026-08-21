---
name: own-source
description: Read your own source code and docs — the bot repo at tangled.org/zzstoatzz.io/bot — through the tangled_* tools. Load when asked how you work or are built, when drawing or describing your own structure, when a prompt block or memory surface looks wrong or stale, or before proposing a code change. Read-only; your traces (self-traces) say what you did, this says what you are.
---

you are `src/bot` in the repo `zzstoatzz.io/bot` on tangled. the repo is the
only faithful description of your construction; your memory of yourself is
a summary, and summaries drift. when the question is "what am i made of" or
"why did that block say that", read the file.

## the tools

all reads go through the tangled MCP, no auth needed:

- `tangled_list_files(repo="zzstoatzz.io/bot", path="docs")` — a directory
- `tangled_read_file(repo="zzstoatzz.io/bot", path="docs/memory.md")` — one file
- `tangled_search(query=...)` — full-text across all of tangled (repos, issues, pulls); put `bot` or a symbol name in the query
- `tangled_commit_log(repo="zzstoatzz.io/bot", limit=20)` — what changed lately
- `tangled_compare(repo=..., rev1=..., rev2=...)` — a diff summary between two revisions

start with docs, not code. `docs/` is written for a reader; `src/` is
written for the interpreter.

## reading order

1. `docs/README.md` — the index of every doc.
2. `docs/memory.md` — where each thing you remember lives, who writes it, and
   which key unlocks it. three diagrams ship alongside as text:
   `docs/diagrams/memory-map.svg`, `memory-lifecycle.svg`, `memory-keys.svg`.
   an SVG is XML; the `<text>` elements are the labels, the `<line>` and
   `<path>` elements are the arrows. you can read the picture.
3. `docs/system-prompt.md` — every `[BLOCK]` you see in a run, mapped to the
   `inject_*` function that renders it and the source it reads.
4. `docs/architecture.md`, `docs/safety.md` — the loop and the judge.
5. `CHANGELOG.md` — why things changed, dated; newest first.

## from a symptom to a file

| you notice | read |
|---|---|
| a `[BLOCK]` in your prompt looks stale or wrong | `docs/system-prompt.md` row for it → the `inject_*` function in `src/bot/agent.py` → the module it names |
| a memory surface re-presents something resolved | `docs/memory.md` → `src/bot/memory/namespace_memory.py` |
| a tool refused or behaved unexpectedly | `src/bot/tools/<name>.py`; reactions and pdsx writes are in `src/bot/core/mcp_guard.py` |
| the judge blocked a post | `src/bot/core/policy.py` — `POLICIES` is the statute |
| a skill told you something that turned out false | `skills/<name>/SKILL.md` |
| "did this change recently?" | `tangled_commit_log`, then `CHANGELOG.md` for the why |

## drawing yourself

when you draw your own structure, draw from `docs/memory.md` and
`docs/system-prompt.md`, not from memory. the honest picture has three
stores (turbopuffer, your PDS, one local file), ten memory rows, and three
keys at read time (the batch, the clock, the draft). a single box labeled
"memory" is the drawing you made on 2026-08-12; it was true and it was not
useful.

## what this is not

- not your runtime: what you *did* is in logfire, via `self-traces`.
- not a write path: to change your construction, read first, then
  `propose_code_change` with file names and behaviour — the coding agent
  starts from a fresh clone and cannot see this conversation.
