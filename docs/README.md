# documentation

deeper dive into phi's design.

## contents

- [architecture.md](architecture.md) — entry points, scheduling, why this shape
- [memory.md](memory.md) — the four kinds of state phi draws on (thread, private, public, intent)
- [system-prompt.md](system-prompt.md) — block-by-block reference for what's actually in phi's context per run
- [mcp.md](mcp.md) — model context protocol integration
- [safety.md](safety.md) — how public actions are bounded: policies + judge, the pdsx guard, the operator override
- [testing.md](testing.md) — testing philosophy
- [skill-or-tool.md](skill-or-tool.md) — the principle for deciding when something is a tool vs a skill, with the review trail
- [tool-sprawl.md](tool-sprawl.md) — module-misplacement backlog from surfacing the catalogue in the UI
- [patterns.md](patterns.md) — recurring lessons from the git history (deletion, feedback loops, DotDict, voice vs structure, silently-empty blocks, prescription in task prompts, attention shapes voice)
- [internal/cockpit.md](internal/cockpit.md) — the web UI (internal, operator-facing)

## reading order

1. **architecture.md** — overall shape
2. **memory.md** — what phi knows and where it lives
3. **system-prompt.md** — exactly what reaches the model on every run
4. **mcp.md** — external capabilities
5. **safety.md** — how public actions are bounded, and why structurally
6. **testing.md** — how we verify behavior
7. **skill-or-tool.md** — the design principle behind the tool/skill split
8. **tool-sprawl.md** — known module misplacements to clean up over time
9. **patterns.md** — recurring lessons; read before refactoring anything that looks accidental

each doc is self-contained and can be read independently.
