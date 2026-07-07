# phi evals

behavioral tests for phi: given a scenario, does the agent reach for the
right tool, route the write to the right place, respect its gates? cheap
model, spied tools, LLM-as-judge where quality matters.

these are not unit tests. they call real models, cost real money, and can
flake on model temperament. they run manually (`just evals`), not in CI.

## running

```bash
just evals                 # everything
uv run pytest evals/ -k <pattern> -v
```

requires `ANTHROPIC_API_KEY` with credit on the account. individual evals
may need more (bluesky credentials, turbopuffer, openai for embeddings) —
they skip when their keys are missing, but a present key with an exhausted
account fails loudly. that's intentional: silence would look like coverage.

## writing one

the pattern that stays useful (see any current eval for a live example):

1. build a small agent that shares as much real surface as possible —
   the actual personality file, the actual skills directory — and spy on
   the tools you'd otherwise mock.
2. drive it with a scenario prompt, in the shape production phi actually
   receives (batched notifications, not bare mentions).
3. assert on the *routing* — which tool fired, what the executed code
   contained — before asserting on prose quality.
4. reach for the LLM judge only for questions structure can't answer.

the failure mode to avoid: hand-building a parallel mini-phi whose prompt
re-declares the tool surface as text. it drifts silently — the eval keeps
passing against an agent that no longer resembles the deployed one. when
production behavior moves (entry points, gating mechanics, tool routing),
the eval fixtures must move with it or be deleted; a green eval against a
stale simulacrum is worse than no eval.

## what deserves eval coverage

whatever currently carries behavioral risk — the judgment calls prompts
are supposed to shape. tool routing, owner-gating, and write-path policy
(what phi saves, when, and why) are the standing categories. enumerate the
specifics in the tests themselves, not here; this file describes the
approach, and docs that list test files rot the moment tests change.
