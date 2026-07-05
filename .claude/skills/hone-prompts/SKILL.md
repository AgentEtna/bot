---
name: hone-prompts
description: >
  Render the system prompts phi actually shipped to prod (from logfire) and
  hone them at their source. Use when the operator wants to review, edit, or
  debug any of phi's system prompts — the main agent or a sub-agent (policy
  judge, extractor, reconciler, episodic synth, posting inventory) — and needs
  to see the real composed prompt, not just the static personality file.
---

# hone-prompts

phi's system prompts are **composed at runtime**, not stored as files. The main
prompt is `personalities/phi.md` + operational instructions + ~15 dynamic
`@system_prompt` callbacks that inject live state (time, notifications, memory,
digests). So the only faithful view of "what actually shipped" is the real
request logfire captured on the way to Anthropic.

This skill renders those real prompts to files, then maps each one back to the
source you edit to change it.

## the six live prompts

Every agent logs a `chat {model}` span whose
`attributes->'gen_ai.input.messages'->0` is the `system` message. Identify the
agent by `attributes->>'gen_ai.agent.name'`.

| agent name (logfire) | what it is | source to edit |
|---|---|---|
| `phi` | the main bot | `personalities/phi.md` (personality) + `_build_operational_instructions` (`agent.py:40`, pulls `core.policy.POLICIES`) + `@system_prompt` callbacks (`agent.py:236-502`) |
| `phi-policy-judge` | pre-action consent/policy check | `core/policy.py:101` |
| `phi-posting-inventory` | self-awareness / posting inventory | `core/self_state.py:69` |
| `phi-episodic-synth` | haiku memory synth | `memory/namespace_memory.py:128` |
| `phi-extractor` | observation extraction | `agent.py:510` |
| `observation-reconciler` | ADD/UPDATE reconciliation | `memory/extraction.py:135` |

## 1. render the static base (this is what you hone)

The system message is `attributes->'gen_ai.input.messages'->0`. Its `parts` array
is **multi-block**: `parts[0]` is the static base (personality + operational
rules — the bulk of what you edit), and `parts[1..N]` are the live outputs of the
dynamic `@system_prompt` callbacks (`[NOW]`, `[DISCOVERY POOL]`, `[ATLAS]`, …).

**You hone source, not a rendered wall of live values.** So the default render is
just `parts[0]` — one clean query, no index-chasing. Run this per agent (swap the
name) via the logfire MCP (`query_run`, project `phi`) and `Write` the result to
`scratch/prompts/{agent}.md`:

```sql
SELECT attributes->'gen_ai.input.messages'->0->'parts'->0->>'content' AS base
FROM records
WHERE span_name LIKE 'chat %'
  AND attributes->>'gen_ai.agent.name' = 'phi'   -- swap per agent
ORDER BY start_timestamp DESC
LIMIT 1
```

Sub-agents (policy-judge, extractor, reconciler, episodic-synth,
posting-inventory) have **only** `parts[0]` — no dynamic blocks — so this is their
entire prompt.

## 1b. inspect which dynamic blocks fired (debugging, not honing)

When you need to see *what phi actually saw* at runtime — a callback that errored,
injected stale data, or blew up the token count — pull a labeled index of the
blocks instead of concatenating them. Each non-empty part is one callback's
output; the leading `[BRACKET]` names it. Query the heads:

```sql
SELECT
  substr(attributes->'gen_ai.input.messages'->0->'parts'->1->>'content',1,60)  AS b1,
  substr(attributes->'gen_ai.input.messages'->0->'parts'->2->>'content',1,60)  AS b2,
  substr(attributes->'gen_ai.input.messages'->0->'parts'->3->>'content',1,60)  AS b3
  -- …extend to ~b16; empty string = a conditional callback that didn't fire
  -- this run (healthy); null = past the end of the array.
FROM records
WHERE span_name LIKE 'chat %' AND attributes->>'gen_ai.agent.name' = 'phi'
ORDER BY start_timestamp DESC LIMIT 1
```

Read the bracket label, map it to its callback (`agent.py:236-502`, grep the
label), pull that one part's full content if it's the suspect. This is a
diagnostic — don't dump all blocks into a file and treat the blob as "the prompt
to hone"; the blob is prod-truth cosplay, the source is what you change.

*(Why not one query for the whole assembled prompt? logfire stores `parts` as
opaque JSON — this DataFusion engine has no working `unnest`/`json_array_length`,
and casting the array to text returns null. The raw `request_data` attribute on
the `Anthropic API call` span holds the entire request as one 100KB+ JSON string,
but it's tools + full history too. So there is no clean whole-prompt pull; the
per-block index above is the honest ceiling.)*

**CRITICAL window gotcha:** `query_run` scans only the **last 30 minutes** unless
you pass `start_timestamp` / `end_timestamp` explicitly (max range 14 days). A
SQL `... > now() - INTERVAL '7 days'` filter does NOT widen it — the params are
ANDed, not widened. If a query returns nothing or suspiciously little, you almost
certainly forgot the window params, not "phi is down." Pass a real window:
`start_timestamp` = a few days back, `end_timestamp` = now.

Sub-agents fire less often (extractor/reconciler run only on the daily-reflection
path). If one returns no rows, widen the window toward 14 days before concluding
it's unused.

## 2. hone at the source

The rendered file is **read-only ground truth** — never edit it and expect prod
to change. Find the offending text in the rendered prompt, locate which source
row above owns it, and edit there:

- static voice / identity / standing rules → `personalities/phi.md`
- policy/consent language → `core/policy.py` (`POLICIES`) — this text appears in
  BOTH the operational instructions of `phi` AND the `phi-policy-judge` prompt,
  so edit once, check both.
- anything wrapped in `[BRACKETS]` (e.g. `[NOW]`, `[NEW NOTIFICATIONS]`,
  `[SEMBLE]`, per-author memory, digests) → a `@system_prompt` callback in
  `agent.py:236-502`. Grep the bracket label to find the exact callback.

After editing, changes only reach prod on the next **deploy** — a rendered
snapshot reflects the running release, not your working tree. To verify an edit,
re-render after the deploy and diff against the pre-edit snapshot.

## 3. keep AGENTS.md honest

This repo enforces prompt↔doc sync (see recent commit history). If a hone changes
observable behavior described in `AGENTS.md` or `docs/`, update the doc in the
same change.

## gotchas

- The tool-call span is named just `running tool`; the tool name is in
  `attributes->>'gen_ai.tool.name'`, NOT in the span name. (Patterns like
  `span_name LIKE '%running tool: post%'` match nothing — a bug that has bitten
  both this skill's author and `phi-check`.)
- `gen_ai.input.messages` is a JSON array; element 0 is the system message. Later
  elements are the conversation and will differ every run — you only want
  element 0 for prompt honing.
- Prompt length drifts with injected state (phi's main prompt runs ~9k chars).
  A sudden large change in rendered length between snapshots is a fast signal
  that a callback started injecting more (or errored out and injected nothing).
