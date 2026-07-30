---
name: phi-prompt-inspect
description: Pull the exact rendered system + user prompts phi worked from on a past agent run, via the logfire MCP. Use when investigating phi's behavior — why she said that, what her context actually contained, is a block stale or feedback-looping. Read-only, no PDS writes.
---

phi is instrumented by pydantic-ai → logfire. every `agent.run()` writes an `agent run` span whose `attributes.pydantic_ai.all_messages` carries the full conversation as sent to the model: system prompt with each dynamic block as a separate `text` part, then user / assistant / tool turns.

## the two queries

Both run via `mcp__logfire__query_run(project="phi", query=..., start_timestamp=..., end_timestamp=...)`. Project is always required. Default window is 30min; max 14d. Narrow it aggressively — phi fires an `agent run` every ~10s (notification poll), so data is dense.

**Step 1 — find the span behind a specific behavior.** `final_result` is phi's own brief string summary of what she did on that run; that's the easiest way to pick the right one out of a dense window:

```sql
SELECT span_id, trace_id, start_timestamp,
       attributes->>'final_result' AS final_result
FROM records
WHERE span_name = 'agent run'
  AND attributes->>'gen_ai.agent.name' = 'phi'
ORDER BY start_timestamp DESC
LIMIT 5
```

Filter on `gen_ai.agent.name = 'phi'` to exclude sub-agents — `phi-inner-critic`, `phi-extractor`, `observation-reconciler` each fire their own `agent run` spans with short purpose-built prompts. Useful when you want them, distracting when you don't.

**Step 2 — pull the rendered prompt** once you have a span_id:

```sql
SELECT attributes->'pydantic_ai.all_messages' AS messages
FROM records
WHERE span_id = '<span_id>'
LIMIT 1
```

## structure of `pydantic_ai.all_messages`

It's an array of messages:

- **`[0]`** = system — `parts` is the array of dynamic blocks in registration order. `parts[0]` is the **static base** (`personalities/phi.md` + cross-cutting operational rules); the rest are the dynamic blocks: `[YOUR INFRASTRUCTURE]`, `[OPERATOR]`, `[NOW]`, `[OPERATIONAL HISTORY]`, `[KNOWN RELAYS]`, `[GOALS AND INTERESTS]` + `[SELF-AWARENESS]` + `[SELF STATE]`, `[RECENT OPERATIONS]`, `[DISCOVERY POOL]`, `[NEW NOTIFICATIONS]`, per-author memory (`[PHI'S SYNTHESIZED IMPRESSION OF @h]` / `[OBSERVATIONS ABOUT @h]` / `[PAST EXCHANGES WITH @h]` / `[BACKGROUND RESEARCH ON @h]`), `[RELEVANT MEMORIES — synthesized for this query]`, `[ATLAS]`, `[DOCKET]`, `[OWNED FEEDS]`, `[SEMBLE]`. See `docs/system-prompt.md` for what each one is for.
- **`[1]`** = user — the path-specific task prompt (notifications batch / cycle / reflection) plus any appended blocks: `[WORKFLOW STATE]`, `[RECENT FLOW MENTIONS]`, `[RECENT CONVERSATIONS]`, `[FIRST INTERACTION WITH @h]`, `[SERVICE HEALTH]`.
- **`[2..]`** = assistant + tool roundtrips inside the agent loop. Inspect these to see which tools phi actually called and with what arguments.

## what to look for

- **Personality dilution.** `parts[0]` should contain `phi.md` verbatim. Compare against the file.
- **Feedback loops.** Several dynamic blocks reflect phi's own past output back at her — `[SELF-AWARENESS]` (sub-agent description of recent posts), `[RECENT OPERATIONS]` (verbatim recent writes), `[DOCKET]` titles (often derived from past observations). If they're all in a register phi.md tells phi *not* to use, the model sees that register reinforced even when the constitution forbids it.
- **Block bloat.** Compare token weight of abstract rules (top of `parts[0]`) against concrete current blocks. Concrete + current usually dominates abstract + general.
- **Tool roundtrips in `[2..]`.** Watch for `recall` / `search_memory` calls and what was returned — sometimes phi's drift comes from what private memory surfaced.

## gotchas

- `agent_name` is also present in attributes (pydantic-ai's own), but `gen_ai.agent.name` is the OTel-standard key the sub-agents also set — filter on that.
- A trace can span multiple `agent run` spans (the main run plus sub-agents triggered during it). `trace_id` groups them; `span_id` identifies one.
- The JSON column accepts `->` for object access and `->>` for text extraction. Array index access (`->0`) and chaining (`->'foo'->0->'bar'`) work in DataFusion just like Postgres.
