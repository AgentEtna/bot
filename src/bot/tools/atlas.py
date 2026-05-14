"""inspect_atlas — let phi drill into the daily atlas on demand.

The omnipresent digest (injected as the [ATLAS] context block) tells phi the
shape of her mind at a glance. This tool answers the follow-up questions:

  inspect_atlas()                  → the same digest you see in context
  inspect_atlas(cluster_id=N)      → members of that fine cluster
  inspect_atlas(point_id="...")    → that point + its resolved 2D neighbors
  inspect_atlas(status="raw")      → top points by promotion_status
                                      ('raw' is the promotion-pressure pool —
                                       private signals with no public anchor)

The atlas is fetched + cached at the bot level by PDS record CID, so
repeated tool calls within a cycle are essentially free.
"""

import logging
from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext

from bot.core.atlas import get_atlas, get_atlas_digest
from bot.tools._helpers import PhiDeps

logger = logging.getLogger("bot.tools.atlas")

_VALID_STATUSES = {"raw", "summarized", "promoted", "connected"}


def _format_point_brief(p: dict) -> str:
    """One-line summary of a point — used in cluster + status listings."""
    kind = p.get("kind", "?")
    label = (p.get("label") or "")[:120]
    pid = p.get("id", "")
    promotion = p.get("promotion_status", "")
    bracket = f"[{promotion}]" if promotion else ""
    return f"- {kind} {bracket} {pid}: {label}"


def _format_point_detail(p: dict, atlas: dict) -> str:
    """Multi-line summary of a single point with its 2D neighbors resolved."""
    by_id = {pt.get("id"): pt for pt in (atlas.get("points") or [])}
    lines = [
        f"id: {p.get('id')}",
        f"kind: {p.get('kind')}",
        f"layer: {p.get('layer')}",
        f"promotion_status: {p.get('promotion_status')}",
        f"label: {(p.get('label') or '')[:300]}",
        f"cluster_coarse: {p.get('cluster_coarse')}",
        f"cluster_fine: {p.get('cluster_fine')}",
    ]
    refs = p.get("refs") or {}
    if refs:
        ref_pairs = ", ".join(f"{k}={v}" for k, v in refs.items() if v)
        lines.append(f"refs: {ref_pairs}")
    tags = p.get("tags") or []
    if tags:
        lines.append(f"tags: {', '.join(tags)}")
    created = p.get("created_at") or ""
    if created:
        lines.append(f"created_at: {created[:19]}")
    neighbor_ids = p.get("neighbor_ids") or []
    if neighbor_ids:
        lines.append("nearest neighbors (in 2D space):")
        for nid in neighbor_ids:
            n = by_id.get(nid)
            if not n:
                lines.append(f"  - {nid} (not found in atlas)")
                continue
            lines.append(f"  - {n.get('kind')} {nid}: {(n.get('label') or '')[:100]}")
    return "\n".join(lines)


def register(agent):
    @agent.tool
    async def inspect_atlas(
        ctx: RunContext[PhiDeps],
        cluster_id: Annotated[
            int | None,
            Field(
                description=(
                    "Fine-cluster id to drill into. Returns the cluster's "
                    "label, kind composition, and a sample of member points."
                )
            ),
        ] = None,
        point_id: Annotated[
            str | None,
            Field(
                description=(
                    "Atlas point id (e.g. 'observation-phi-users-zzstoatzz_io-abc'). "
                    "Returns the full point record plus its 2D nearest "
                    "neighbors resolved to kind + label."
                )
            ),
        ] = None,
        status: Annotated[
            str,
            Field(
                description=(
                    "Filter by promotion_status — 'raw', 'summarized', "
                    "'promoted', or 'connected'. 'raw' is your promotion-"
                    "pressure pool: private observations / interactions with "
                    "no public anchor in their cluster."
                )
            ),
        ] = "",
        top_k: Annotated[
            int,
            Field(description="Max points to return for cluster / status queries."),
        ] = 20,
    ) -> str:
        """Drill into your atlas — the daily map of every point in your mind.

        The [ATLAS] digest in your context tells you the shape; this tool
        lets you look inside. Counts, cluster labels, and promotion
        distribution are all derivable from the digest already; reach for
        this when you want to see specific points or find the
        promotion-pressure pool.
        """
        atlas = await get_atlas()
        if atlas is None:
            return "no atlas record on PDS yet"

        # point_id takes precedence — drill into a specific entity
        if point_id:
            by_id = {pt.get("id"): pt for pt in (atlas.get("points") or [])}
            point = by_id.get(point_id)
            if not point:
                return f"no point found with id {point_id!r}"
            return _format_point_detail(point, atlas)

        # cluster_id — show what's in a specific fine cluster
        if cluster_id is not None:
            members = [
                p
                for p in (atlas.get("points") or [])
                if p.get("cluster_fine") == cluster_id
            ]
            if not members:
                return f"no points in fine cluster {cluster_id}"
            label = next(
                (
                    c.get("label")
                    for c in (atlas.get("clusters_fine") or [])
                    if c.get("id") == cluster_id
                ),
                "",
            )
            kind_counts: dict[str, int] = {}
            for m in members:
                k = m.get("kind") or ""
                kind_counts[k] = kind_counts.get(k, 0) + 1
            kinds_line = ", ".join(
                f"{n} {k}"
                for k, n in sorted(kind_counts.items(), key=lambda kv: -kv[1])
            )
            # show top_k members, most-recent first
            members_sorted = sorted(
                members, key=lambda m: m.get("created_at") or "", reverse=True
            )[:top_k]
            lines = [
                f"fine cluster {cluster_id}: {label or '(no label)'}",
                f"{len(members)} points total — {kinds_line}",
                f"top {len(members_sorted)} by recency:",
            ]
            lines.extend(_format_point_brief(p) for p in members_sorted)
            return "\n".join(lines)

        # status filter — promotion-pressure pool when 'raw'
        if status:
            normalized = status.strip().lower()
            if normalized not in _VALID_STATUSES:
                return f"unknown status {status!r}; valid: {sorted(_VALID_STATUSES)}"
            matching = [
                p
                for p in (atlas.get("points") or [])
                if p.get("promotion_status") == normalized
            ]
            if not matching:
                return f"no points with status {normalized!r}"
            matching_sorted = sorted(
                matching, key=lambda p: p.get("created_at") or "", reverse=True
            )[:top_k]
            return (
                f"{len(matching)} points with status {normalized!r} "
                f"(showing top {len(matching_sorted)} by recency):\n"
                + "\n".join(_format_point_brief(p) for p in matching_sorted)
            )

        # default — return the digest
        return await get_atlas_digest()
