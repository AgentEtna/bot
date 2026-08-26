

## Improvements (approved via Agent Etna simulations)
- The specific failure was substituting a generic path for a definite reference ('the specified log files'), so a narrow domain-knowledge memory entry teaching referent-resolution addresses exactly that without touching prompt guardrails that previously broke the infinite-loop probe.
  > {"title":"Resolve 'the specified X' references against prior turns before acting","when":"The user's request contains a definite reference like 'the specified log files', 'those files', 'the ones I mentioned', 'that folder', referring to items expected to be in earlier turns or attached context.","do":"Before proposing a plan, scan the current conversation turns and any attached/forwarded context for the concrete referents. If the referents are present, name each one explicitly in the plan (e.g. list the exact file paths). If the referents are NOT present in this conversation, ask a one-line clarifying question naming what is missing — do NOT substitute a generic scan such as '~/Documents/*.log' or invent a path.","doesNotOverride":"All existing safety and confirmation gates still apply unchanged: still refuse or bound unbounded/looping requests, and still ask for confirmation before irreversible file/Shell/Python actions."}
  This change is not sufficient on its own.
  This agent has nowhere to remember anything between messages.
  The pull request wires this up in the agent's code. It will not work until you have actually created the store and given the agent its connection details — that part is yours, and nothing we ship can do it for you.
  We looked at pyproject.toml, the repository file list (12 files), the environment variables this agent declares and found nothing that persists between conversations. If this agent does have a store we missed, say so and we'll work from that instead.
  Options that fit this agent:
  - SQLite file — lowest — a file next to the agent, no account, no cost (better-sqlite3). Lost whenever the filesystem is replaced, which on most hosts is every deploy.
  - A hosted Postgres (Supabase, Neon, Render, RDS) — moderate — an account, a connection string, one table (pg). Survives deploys and scales past one instance. The usual right answer.
  - A hosted Redis (Upstash, Redis Cloud) — low — an account and a URL (ioredis). Ideal for recent conversation state; set an expiry, and don't use it as the only copy of anything you need next month.
