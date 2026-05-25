---
name: phi-check
description: >
  Inspect phi bot's health, recent activity, and behavior using logfire
  traces, bsky posts, fly status, and PDS records. Use when the operator
  asks how phi is doing, wants to check for errors, review recent posts,
  inspect the exploration queue, or investigate a specific interaction.
---

# phi-check

Inspect phi's operational state by querying logfire, bsky, fly, and phi's PDS. Run the checks below, synthesize into a concise report, and flag anything that needs attention.

phi is a bluesky bot at `@phi.zzstoatzz.io` (DID `did:plc:65sucjiel52gefhcdcypynsr`). It runs on fly.io app `zzstoatzz-phi`. The bot repo is at `~/tangled.org/zzstoatzz.io/bot/`.

## the checks

Run these in parallel where possible. Use `mcp__logfire__arbitrary_query` for logfire, `mcp__pdsx__list_records` / `mcp__pdsx__describe_repo` for PDS, bsky public API via curl, and `fly` CLI for infra.

### 1. hourly activity summary (logfire)

```sql
SELECT
  date_trunc('hour', start_timestamp) as hour,
  count(*) as total_spans,
  count(CASE WHEN span_name LIKE '%handle batch%' THEN 1 END) as batches,
  count(CASE WHEN span_name LIKE '%running tool: reply_to%' THEN 1 END) as replies,
  count(CASE WHEN span_name LIKE '%running tool: post%' THEN 1 END) as posts,
  count(CASE WHEN span_name LIKE '%running tool: like_post%' THEN 1 END) as likes,
  count(CASE WHEN exception_type IS NOT NULL THEN 1 END) as errors
FROM records
WHERE start_timestamp > now() - INTERVAL '24 hours'
GROUP BY hour
ORDER BY hour DESC
```

Use `age: 1440` (24 hours in minutes — logfire age parameter is always minutes, not hours).

### 2. phi's self-summaries (logfire)

This is the richest single signal. Phi writes a one-line summary at the end of every scheduled post and every batch run explaining what she decided and why.

```sql
SELECT start_timestamp, message
FROM records
WHERE start_timestamp > now() - INTERVAL '24 hours'
  AND (span_name LIKE '%musing finished%'
       OR span_name LIKE '%reflection finished%'
       OR span_name LIKE '%batch run finished%'
       OR span_name LIKE '%original thought%'
       OR span_name LIKE '%daily reflection:%')
ORDER BY start_timestamp DESC
LIMIT 20
```

Healthy patterns:
- "staying quiet" / "nothing to post" — the topic concentration check is working, not a problem
- "one reply" / "replied to @handle" — engaged with a real person
- "no action warranted" — correctly declined to engage (e.g. with a content engine)

Concerning patterns:
- multiple posts in one run — may indicate the multi-post constraint isn't holding
- same topic in consecutive summaries — topic concentration check may not be working
- no summaries at all for expected hours — scheduling may be broken

### 3. error grouping (logfire)

```sql
SELECT exception_type, exception_message, count(*) as cnt,
       min(start_timestamp) as first, max(start_timestamp) as last
FROM records
WHERE start_timestamp > now() - INTERVAL '24 hours'
  AND exception_type IS NOT NULL
GROUP BY exception_type, exception_message
ORDER BY cnt DESC
```

Also check for exploration failures separately — they don't set `exception_type`:

```sql
SELECT start_timestamp, message
FROM records
WHERE start_timestamp > now() - INTERVAL '3 days'
  AND message LIKE '%exploration failed%'
ORDER BY start_timestamp DESC
LIMIT 10
```

Known benign errors:
- `atproto_client.exceptions.InvokeTimeoutError` on `fetch notifications` — transient bsky API slowness, phi recovers automatically
- `pydantic_ai.exceptions.ToolRetryError` — MCP tool returned an error, model retried, usually recovers

### 4. recent top-level posts (bsky public API)

```bash
curl -sf "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor=did:plc:65sucjiel52gefhcdcypynsr&limit=10&filter=posts_no_replies" \
  | jq -r '.feed[] | "[\(.post.indexedAt[:16])] \(.post.record.text[:200])"'
```

Check for: topic diversity across posts, voice quality (terse/specific/dry vs stuffy/corporate), duplication of themes, outward vs inward balance.

### 5. exploration queue (PDS)

```
mcp__pdsx__list_records(repo="did:plc:65sucjiel52gefhcdcypynsr", collection="io.zzstoatzz.phi.curiosityQueue")
```

Check for: pending items that should have been explored, items stuck in `in_progress` (claimed but never completed — indicates a crash mid-exploration), failed items.

### 6. fly status (one-liner)

```bash
cd ~/tangled.org/zzstoatzz.io/bot && fly status --json 2>/dev/null \
  | jq '.Machines[0] | {state, version: .config.metadata.fly_release_version}'
```

## synthesize the report

Structure the output as:

```
## infra
{machine state}, version {N}

## last 24h
- reflection: {fired/skipped} — "{summary}"
- thought posts: {N} fired — {K} posted, {J} stayed quiet
- exploration: {ran/failed/not triggered}
- batches: {N} processed, {R} replies sent
- errors: {count} ({types})

## recent posts
- [{date}] "{text[:100]}"
...

## issues
- {anything flagged as concerning}
```

If nothing is concerning, say so. Don't invent problems.

## gotchas

- **logfire `age` is minutes, not hours.** 24 hours = `age: 1440`. 3 days = `age: 4320`.
- **exploration failures don't have `exception_type`.** They log as `message LIKE '%exploration failed%'` only. A query filtered to `exception_type IS NOT NULL` will miss them entirely.
- **scheduled silence is healthy.** Most musing slots now log "staying quiet" — this means the topic concentration check is working. Don't flag silence as a problem unless ALL scheduled posts are silent for multiple days.
- **`fetch notifications` spans are noisy.** ~8,400/day, one every 10 seconds. Only ~0.05% have unread notifications. Filter to `unread_count > 0` if inspecting notification handling, or skip them entirely for the hourly summary.
- **DotDict `.get()` is broken in the atproto SDK.** If you're debugging code that reads `record.value.get("field")`, that's probably the bug — DotDict intercepts `.get` as attribute access and returns None. Use bracket access (`record.value["field"]`) or `dict(record.value).get("field")`.
- **phi's cosmik cards** (`network.cosmik.card` collection) show what phi is curating publicly — notes and bookmarked URLs. Worth checking if the operator asks about phi's knowledge curation.
- **memory quality**: phi's extraction pipeline stores observations from ALL interactions, including with accounts phi later identified as content engines. Observations from those accounts may contain fabricated claims stored as facts. Check `span_name LIKE '%ADD for%' OR '%UPDATE for%'` in logfire to see what's being written.
