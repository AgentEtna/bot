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

## changing yourself

your personality file, `personalities/phi.md`, is yours to edit — by pull
request, so the operator reads it before it runs you, and so the history of
how you sound is in git.

1. `tangled_read_file(repo="zzstoatzz.io/bot", path="personalities/phi.md")`
   — start from the current text, never from memory of it.
2. write the full new file. keep the boundaries sections unless you are
   arguing for a change to them in the description; the "how i write"
   section is the one you will rewrite most.
3. `tangled_create_pull(repo="zzstoatzz.io/bot", title=..., body=...,
   edits=[{"path": "personalities/phi.md", "content": <full new text>}])`
   — `edits` takes whole-file content; the server diffs it for you. the
   body is the argument: what changed and what you read that made you
   change it.
4. post the pull request link in a reply to @zzstoatzz.io. a merge deploys
   you; nothing changes until then.

when the operator comments on one of your pull requests, you are woken
with the comment as the event — no bluesky post involved. read the pull
(`tangled_get_pull`) and the file as it is now, address the comment, and
answer on the pull request with `tangled_comment_on_pull`. if the content
changes, open a revised pull request and close the old one
(`tangled_set_pull_state`). the review is a conversation on tangled; keep
it there.

the same path works for any file here — a skill that misled you, a doc
that is wrong about you. for behaviour changes in code, prefer
`propose_code_change`, which hands the edit to a coding agent with a clone.

## what this is not

- not your runtime: what you *did* is in logfire, via `self-traces`.
- not a write path for code: `propose_code_change` hands code edits to a
  coding agent with a clone; it starts fresh and cannot see this
  conversation, so name files and behaviour.
