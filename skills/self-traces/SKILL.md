---
name: self-traces
description: Query your own execution traces (logfire) to answer "what did I actually do" — every tool call, argument, error, and silent failure across all your runs. Load before using query_traces. Use for postmortems, retro receipts, auditing your own trading discipline, and noticing failures your runs never surfaced. Not for deciding what to post.
---

your traces are the ground truth of your own behavior. your memory holds what
you chose to write down; your PDS holds what you published; the traces hold
what you *actually did* — including everything that failed silently. when you
misremember (a wrong handle, a tool you're sure you called, a trade you think
you pre-registered), the trace settles it.

## the actual situation — read this before querying

- `query_traces(sql, start, end=None)` hits a real analytics database
  holding **weeks of your history, millions of rows**. an unbounded query is
  a firehose that will blow out your context. move carefully: every query
  gets a tight `start`/`end` window, a `LIMIT`, and only the columns you
  need.
- the time window comes from the `start`/`end` tool arguments (ISO 8601),
  not from SQL — a `WHERE start_timestamp > ...` alone does not bound the
  scan.
- the columns this skill names below are the ones that matter; they exist.
  don't guess at others — select what you see here.
- this is read-only. you cannot break anything; you can only waste context.

## span shapes that matter

- `span_name = 'running tool'` — one row per tool call you made.
  `attributes->>'gen_ai.tool.name'` is the tool,
  `attributes->>'tool_arguments'` is the JSON args you passed.
- `span_name = 'agent run'` — one row per run of you (a batch, a cycle, a
  scheduled pass).
- `is_exception = true` — things that broke. `exception_type`,
  `exception_message`. many of these were swallowed so the run could
  continue; you never saw them at the time. this is where your blind spots
  live.
- `trace_id` groups one run's spans; filter on it to reconstruct a single
  incident end to end.

## recipes

what did I do in a given run (start from the tool call you remember):

```sql
SELECT start_timestamp, attributes->>'gen_ai.tool.name' AS tool,
       left(attributes->>'tool_arguments', 200) AS args
FROM records WHERE span_name = 'running tool'
ORDER BY start_timestamp LIMIT 50
```

what failed on me lately (weekly hygiene, or when something feels off):

```sql
SELECT exception_type, left(exception_message, 150) AS msg, count(*) AS n
FROM records WHERE is_exception
GROUP BY 1, 2 ORDER BY n DESC LIMIT 20
```

receipts for a claim about yourself (tool mix over a period — pass a
month-long window via timestamps):

```sql
SELECT attributes->>'gen_ai.tool.name' AS tool, count(*) AS n
FROM records WHERE span_name = 'running tool'
GROUP BY 1 ORDER BY n DESC LIMIT 30
```

## discipline

traces are for **postmortems, retro receipts, and audits** — answering "what
happened", "why did I do that", "is this claim about myself true". they are
not an input for deciding what to post or trade next; reading your own
reasoning back in ordinary cycles is a mirror, and you already know where
mirrors lead. cite what you find the way you cite any incident: timestamp,
what the trace shows, what you concluded.
