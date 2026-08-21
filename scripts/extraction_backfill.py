"""One-off: run phi's observation extraction over interactions it never saw.

Until 2026-08-20, NamespaceMemory listed user namespaces from the first
turbopuffer page only (100 of 167), so every handle sorting after "museical"
was skipped by get_unprocessed_interactions. The daily pass then takes the
five newest interactions per namespace and — because "unprocessed" means
"newer than the latest observation" — one pass marks everything older as
done. For the devlog (147 missed, since 2026-05-23) and the operator (29,
since 2026-06-09) that would silently discard the backlog.

This runs the same extractor and the same reconciliation over the missed
interactions, oldest first, in chunks per handle, so her record of those
people catches up instead of skipping three months. Adds only; never edits
or deletes an existing row.

Run from the bot/ directory:

    uv run python scripts/extraction_backfill.py            # dry run: counts
    uv run python scripts/extraction_backfill.py --apply    # extract + reconcile
"""

import argparse
import asyncio
import logging
import os
from pathlib import Path

from pydantic_ai import Agent

from bot.config import settings
from bot.memory.extraction import EXTRACTION_SYSTEM_PROMPT, ExtractionResult
from bot.memory.namespace_memory import NamespaceMemory

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("backfill")
log.addHandler(logging.StreamHandler())
log.setLevel(logging.INFO)
log.propagate = False

CHUNK = 8


def missed_interactions(mem: NamespaceMemory, ns_id: str) -> list[dict]:
    ns = mem.client.namespace(ns_id)
    obs = ns.query(
        rank_by=("created_at", "desc"),
        top_k=1,
        filters=[
            "And",
            [["kind", "Eq", "observation"], ["status", "NotEq", "superseded"]],
        ],
        include_attributes=["created_at"],
    )
    latest = obs.rows[0].created_at if obs.rows else ""
    try:
        ints = ns.query(
            rank_by=("created_at", "desc"),
            top_k=1000,
            filters={"kind": ["Eq", "interaction"]},
            include_attributes=["content", "created_at", "source_uris"],
        )
    except Exception as e:
        if "source_uris" not in str(e):
            raise
        # pre-provenance namespace: same rows, no citation field
        ints = ns.query(
            rank_by=("created_at", "desc"),
            top_k=1000,
            filters={"kind": ["Eq", "interaction"]},
            include_attributes=["content", "created_at"],
        )
    rows = [
        {
            "content": r.content,
            "created_at": r.created_at,
            "source_uris": list(getattr(r, "source_uris", []) or []),
        }
        for r in ints.rows
        if r.created_at > latest
    ]
    rows.sort(key=lambda r: r["created_at"])
    return rows


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    mem = NamespaceMemory(api_key=settings.turbopuffer_api_key)
    prefix = f"{mem.NAMESPACES['users']}-"
    backlog: dict[str, list[dict]] = {}
    for ns_id in mem._user_namespace_ids():
        try:
            rows = missed_interactions(mem, ns_id)
        except Exception as e:
            log.info(f"skipping {ns_id}: {str(e)[:80]}")
            continue
        if rows:
            backlog[ns_id.removeprefix(prefix).replace("_", ".")] = rows

    total = sum(len(v) for v in backlog.values())
    log.info(f"{len(backlog)} handles, {total} missed interactions")
    for handle, rows in sorted(backlog.items(), key=lambda kv: -len(kv[1])):
        log.info(
            f"  @{handle}: {len(rows)}  {rows[0]['created_at'][:10]} .. {rows[-1]['created_at'][:10]}"
        )
    if not args.apply:
        await mem.close()
        return

    for var, val in (
        ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
        ("OPENAI_API_KEY", settings.openai_api_key),
    ):
        if val and not os.environ.get(var):
            os.environ[var] = val
    extractor = Agent[None, ExtractionResult](
        name="phi-extractor",
        model=settings.agent_model,
        system_prompt=f"{Path(settings.personality_file).read_text()}\n\n{EXTRACTION_SYSTEM_PROMPT}",
        output_type=ExtractionResult,
    )
    stored = 0
    for handle, rows in backlog.items():
        for i in range(0, len(rows), CHUNK):
            chunk = rows[i : i + CHUNK]
            uris = list(dict.fromkeys(u for r in chunk for u in r["source_uris"]))
            prompt = f"recent exchanges with @{handle}:\n\n" + "\n\n---\n\n".join(
                r["content"] for r in chunk
            )
            try:
                result = await extractor.run(prompt)
            except Exception as e:
                log.warning(f"@{handle} chunk {i // CHUNK}: extraction failed: {e}")
                continue
            for obs in result.output.observations:
                if not obs.source_uris and uris:
                    obs.source_uris = uris
                try:
                    await mem._reconcile_observation(handle, obs)
                    stored += 1
                except Exception as e:
                    log.warning(f"@{handle}: reconciliation failed: {e}")
            log.info(
                f"@{handle} chunk {i // CHUNK + 1}/{-(-len(rows) // CHUNK)}: "
                f"{len(result.output.observations)} observations"
            )
    log.info(f"done: {stored} observations reconciled")
    await mem.close()


if __name__ == "__main__":
    asyncio.run(main())
