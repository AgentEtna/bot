# documentation

deeper dive into phi's design.

## contents

- [architecture.md](architecture.md) — entry points, scheduling, why this shape
- [memory.md](memory.md) — the four kinds of state phi draws on (thread, private, public, intent)
- [system-prompt.md](system-prompt.md) — block-by-block reference for what's actually in phi's context per run
- [mcp.md](mcp.md) — model context protocol integration
- [testing.md](testing.md) — testing philosophy
- [skill-or-tool.md](skill-or-tool.md) — the principle for deciding when something is a tool vs a skill, with the review trail
- [tool-sprawl.md](tool-sprawl.md) — module-misplacement backlog from surfacing the catalogue in the UI

## reading order

1. **architecture.md** — overall shape
2. **memory.md** — what phi knows and where it lives
3. **system-prompt.md** — exactly what reaches the model on every run
4. **mcp.md** — external capabilities
5. **testing.md** — how we verify behavior
6. **skill-or-tool.md** — the design principle behind the tool/skill split
7. **tool-sprawl.md** — known module misplacements to clean up over time

each doc is self-contained and can be read independently.
