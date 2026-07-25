"""query_traces — phi reads her own execution traces (logfire).

One read-only tool over logfire's query API. The traces are the ground
truth of phi's behavior: every tool call, argument, and swallowed error
across all her runs. The self-traces skill carries the wayfinding (span
shapes, recipes, discipline); this module is just the pipe.
"""

import logging
from typing import Annotated

import httpx
from pydantic import Field

from bot.config import settings

logger = logging.getLogger("bot.tools.traces")

_QUERY_URL = "https://logfire-us.pydantic.dev/v1/query"
_MAX_OUTPUT_CHARS = 6000


def _render_columnar(payload: dict) -> str:
    """Transpose logfire's columnar response into readable rows."""
    columns = payload.get("columns") or []
    if not columns:
        return "no rows"
    names = [c["name"] for c in columns]
    values = [c.get("values") or [] for c in columns]
    n_rows = max((len(v) for v in values), default=0)
    if n_rows == 0:
        return "no rows"
    lines = [" | ".join(names)]
    for i in range(n_rows):
        lines.append(" | ".join(str(v[i]) if i < len(v) else "" for v in values))
    out = "\n".join(lines)
    if len(out) > _MAX_OUTPUT_CHARS:
        out = out[:_MAX_OUTPUT_CHARS] + f"\n... truncated ({n_rows} rows total)"
    return out


def register(agent):
    @agent.tool_plain
    async def query_traces(
        sql: Annotated[
            str,
            Field(
                description=(
                    "SQL SELECT over the `records` table (DataFusion engine, "
                    "postgres-like). Always include a LIMIT and only the "
                    "columns you need."
                )
            ),
        ],
        start: Annotated[
            str,
            Field(
                description=(
                    "ISO 8601 start of the time window, e.g. "
                    "'2026-07-16T00:00:00Z'. Required — the database holds "
                    "weeks of history and unbounded queries flood context."
                )
            ),
        ],
        end: Annotated[
            str | None,
            Field(description="ISO 8601 end of the window. Defaults to now."),
        ] = None,
    ) -> str:
        """Query your own execution traces — the ground truth of what you did.

        Every run of you leaves spans: `span_name = 'running tool'` rows carry
        `attributes->>'gen_ai.tool.name'` and `attributes->>'tool_arguments'`;
        `is_exception = true` rows are errors, many of which were swallowed so
        the run could continue — you never saw them at the time. Load the
        self-traces skill for the span shapes, recipes, and discipline before
        first use in a run.

        This is for postmortems, retro receipts, and audits ("what did I
        actually do", "what failed on me", "is this claim about myself
        true") — not an input for deciding what to post.
        """
        token = settings.logfire.read_token
        if not token:
            return "trace access is not configured (no read token)"
        if sql.lstrip().split(None, 1)[0].lower() not in ("select", "with"):
            return "only SELECT queries are supported"
        params: dict[str, str] = {"sql": sql, "min_timestamp": start}
        if end:
            params["max_timestamp"] = end
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    _QUERY_URL,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
            if resp.status_code >= 400:
                return f"query failed ({resp.status_code}): {resp.text[:300]}"
            return _render_columnar(resp.json())
        except Exception as e:
            logger.warning(f"query_traces failed: {e}")
            return f"trace query unavailable right now ({type(e).__name__})"
