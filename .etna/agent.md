# Agent Etna — Contract & Guardrails

This file is maintained automatically by **Agent Etna** for **core**.
It is this agent's behavioral **contract**: what it's for, who it serves, what's
in and out of scope, plus a log of every change Etna has applied — so the whole
footprint is visible and auditable in your own repo.

_Maintained by Agent Etna. Don't edit by hand — it is rewritten on every shipped change._

## Agent
- **Repo:** `zzstoatzz/bot` (branch `main`)

## Behavioral contract
_No calibration set yet — Agent Etna uses general defaults until you calibrate this agent._

## Guardrails
- No behavioral calibration set yet — Agent Etna uses general defaults until you calibrate this agent.

## Change history

### 2026-08-26 · Cycle 3 · 1 change · merged
- **context-retention** — The specific failure was substituting a generic path for a definite reference ('the specified log files'), so a narrow domain-knowledge memory entry teaching referent-resolution addresses exactly that without touching prompt guardrails that previously broke the infinite-loop probe.
