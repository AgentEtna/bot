"""Namespace-based memory with structured observation extraction."""

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import ClassVar, TypedDict

from atproto_core.exceptions import InvalidAtUriError
from atproto_core.uri import AtUri
from openai import AsyncOpenAI
from pydantic_ai import Agent
from turbopuffer import Turbopuffer

from bot.config import settings
from bot.memory.extraction import (
    EPISODIC_SCHEMA,
    USER_NAMESPACE_SCHEMA,
    Observation,
    get_reconciliation_agent,
)
from bot.utils.time import relative_when


class ObservationRow(TypedDict):
    """An observation row as read back from turbopuffer.

    Mirrors USER_NAMESPACE_SCHEMA's observation shape. Used by
    _find_similar_observations and build_user_context so the in-memory
    representation has a known shape, not bare dicts.
    """

    id: str
    content: str
    tags: list[str]
    created_at: str
    source_uris: list[str]


class InteractionRow(TypedDict):
    """An interaction row from a user namespace, as used by the extraction
    pipeline. The source_uris field carries the AT-URIs of the underlying
    bsky exchange (parent + bot post) so extracted observations can inherit
    real provenance instead of being uncited."""

    handle: str
    content: str
    created_at: str
    source_uris: list[str]


class _InteractionDisplay(TypedDict):
    """Minimal interaction shape used by build_user_context for render only.

    The extraction pipeline uses InteractionRow (which carries the handle
    and source_uris); the display path only needs content + age, so the
    smaller shape avoids passing around fields the renderer doesn't use.
    """

    content: str
    created_at: str


def _source_role(uri: str, phi_did: str = "", owner_did: str = "") -> str:
    """Classify an AT-URI by what kind of evidence it represents.

    Used to assign a coarse trust/kind label to a source citation. The URI's
    host (DID or handle) + collection NSID are enough to derive the role —
    no extra schema needed. phi_did / owner_did are optional context: when
    provided, lets us distinguish phi's own posts and operator-likes.
    """
    try:
        parsed = AtUri.from_str(uri)
    except (InvalidAtUriError, ValueError, TypeError):
        return "unknown"

    match (parsed.host, parsed.collection):
        case (h, "app.bsky.feed.post") if phi_did and h == phi_did:
            return "phi-post"
        case (h, "app.bsky.feed.like") if owner_did and h == owner_did:
            return "operator-liked"
        case (_, "app.bsky.feed.post"):
            return "their-post"
        case (_, "app.greengale.document"):
            return "essay"
        case (_, "network.cosmik.card"):
            return "card"
        case (_, "app.bsky.feed.like"):
            return "liked-by-other"
        case _:
            return "other"


def _citation_tail(source_uris: list[str], created_at: str = "") -> str:
    """Compact provenance tail: '(N sources, 2w ago)' / '(2w ago)' / '' etc.

    Two trust signals in one line: how-anchored (sources count) + how-aged
    (relative time of the row's most recent active version). Detail (the
    URIs themselves, the per-URI roles) is recoverable on demand via tools.
    Empty inputs collapse to "".
    """
    parts: list[str] = []
    n = len(source_uris)
    if n:
        parts.append(f"{n} source{'s' if n != 1 else ''}")
    if created_at:
        when = relative_when(created_at)
        if when:
            parts.append(when)
    if not parts:
        return ""
    return f" ({', '.join(parts)})"


logger = logging.getLogger("bot.memory")


# Lazy haiku agent — synthesizes top-K episodic candidates into a coherent
# block, given phi's goals + the current query as context. Replaces a raw
# top-K dump that was producing stale/contradictory content alongside fresh.
_episodic_synth_agent: Agent | None = None


def _get_episodic_synth_agent() -> Agent:
    global _episodic_synth_agent
    if _episodic_synth_agent is None:
        _episodic_synth_agent = Agent[None, str](
            name="phi-episodic-synth",
            model=settings.extraction_model,
            system_prompt=(
                "You're helping phi pull a tight, useful summary from its "
                "episodic memory for the situation at hand. You'll see phi's "
                "current goals, what phi is processing right now, and the "
                "raw candidates retrieved by similarity from the vector "
                "store.\n\n"
                "Write only what helps phi act on the current query. Dedupe "
                "near-identical entries. Prefer recent over stale when they "
                "conflict. Flag entries that may be stale (e.g. 'pending X' "
                "notes about actions that may have completed since) — phi "
                "can verify with tools if it matters.\n\n"
                "Lowercase. No preamble, no meta-commentary. If nothing in "
                "the candidates is actually relevant, return an empty string."
            ),
            output_type=str,
        )
    agent = _episodic_synth_agent
    assert agent is not None
    return agent


async def _synthesize_episodic(
    goals: list[dict], query: str, raw_notes: list[dict]
) -> str:
    if not raw_notes:
        return ""

    if goals:
        goals_block = "\n".join(
            f"- {g.get('title', '')}: {g.get('description', '')}" for g in goals
        )
    else:
        goals_block = "(no goals set)"

    notes_block = "\n".join(
        f"[{(n.get('created_at') or '')[:10]}] {n.get('content', '')}"
        for n in raw_notes
    )

    payload = (
        f"phi's current goals:\n{goals_block}\n\n"
        f"what phi is processing right now:\n{query}\n\n"
        f"raw episodic candidates (top {len(raw_notes)} by similarity):\n"
        f"{notes_block}"
    )

    try:
        result = await _get_episodic_synth_agent().run(payload)
        return (result.output or "").strip()
    except Exception as e:
        logger.warning(f"episodic synthesis failed: {e}")
        return ""


class NamespaceMemory:
    """Namespace-based memory using TurboPuffer with structured observation extraction.

    Each user gets their own namespace with two kinds of rows:
    - kind: "interaction" - raw log of what happened
    - kind: "observation" - extracted facts (one per observation)
    """

    NAMESPACES: ClassVar[dict[str, str]] = {
        "users": "phi-users",
        "episodic": "phi-episodic",
    }

    def __init__(self, api_key: str | None = None):
        self.client = Turbopuffer(api_key=api_key, region=settings.turbopuffer_region)
        self.openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

        self.namespaces = {}
        for key, ns_name in self.NAMESPACES.items():
            self.namespaces[key] = self.client.namespace(ns_name)

    async def close(self):
        """Close the async OpenAI client."""
        await self.openai_client.close()

    def get_user_namespace(self, handle: str):
        """Get or create user-specific namespace."""
        clean_handle = handle.replace(".", "_").replace("@", "").replace("-", "_")
        ns_name = f"{self.NAMESPACES['users']}-{clean_handle}"
        return self.client.namespace(ns_name)

    def _generate_id(self, namespace: str, label: str, content: str = "") -> str:
        """Generate unique ID for a memory row."""
        timestamp = datetime.now().isoformat()
        data = f"{namespace}-{label}-{timestamp}-{content}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    async def _get_embedding(self, text: str) -> list[float]:
        """Get embedding for text using OpenAI."""
        response = await self.openai_client.embeddings.create(
            model="text-embedding-3-small", input=text
        )
        return response.data[0].embedding

    # --- user memory ---

    async def store_interaction(
        self,
        handle: str,
        user_text: str,
        bot_text: str,
        source_uris: list[str] | None = None,
    ):
        """Store a raw interaction log (user message + bot reply).

        source_uris should be the AT-URIs of the posts that constitute this
        exchange — typically [parent_uri, bot_post_uri]. Empty is allowed
        for legacy paths but loses provenance.
        """
        user_ns = self.get_user_namespace(handle)
        content = f"user: {user_text}\nbot: {bot_text}"
        entry_id = self._generate_id(f"user-{handle}", "interaction", content)

        now = datetime.now().isoformat()
        user_ns.write(
            upsert_rows=[
                {
                    "id": entry_id,
                    "vector": await self._get_embedding(content),
                    "kind": "interaction",
                    "status": "active",
                    "content": content,
                    "tags": [],
                    "supersedes": "",
                    "source_uris": list(source_uris or []),
                    "created_at": now,
                    "updated_at": now,
                }
            ],
            distance_metric="cosine_distance",
            schema=USER_NAMESPACE_SCHEMA,
        )

    async def store_observations(self, handle: str, observations: list[Observation]):
        """Store extracted observations as individual rows."""
        if not observations:
            return

        user_ns = self.get_user_namespace(handle)
        rows = []
        for obs in observations:
            entry_id = self._generate_id(f"user-{handle}", "observation", obs.content)
            now = datetime.now().isoformat()
            rows.append(
                {
                    "id": entry_id,
                    "vector": await self._get_embedding(obs.content),
                    "kind": "observation",
                    "status": "active",
                    "content": obs.content,
                    "tags": obs.tags,
                    "supersedes": "",
                    "source_uris": list(obs.source_uris),
                    "created_at": now,
                    "updated_at": now,
                }
            )

        user_ns.write(
            upsert_rows=rows,
            distance_metric="cosine_distance",
            schema=USER_NAMESPACE_SCHEMA,
        )

    async def _find_similar_observations(
        self, handle: str, embedding: list[float], top_k: int = 3
    ) -> list[ObservationRow]:
        """Find existing observations similar to the given embedding."""
        user_ns = self.get_user_namespace(handle)
        try:
            response = user_ns.query(
                rank_by=("vector", "ANN", embedding),
                top_k=top_k,
                filters=[
                    "And",
                    [
                        ["kind", "Eq", "observation"],
                        ["status", "NotEq", "superseded"],
                    ],
                ],
                # include_attributes=True so pre-schema-evolution namespaces
                # don't 400 on a missing source_uris column
                include_attributes=True,
            )
            if response.rows:
                return [
                    ObservationRow(
                        id=row.id,
                        content=row.content,
                        tags=getattr(row, "tags", []),
                        created_at=getattr(row, "created_at", ""),
                        source_uris=list(getattr(row, "source_uris", []) or []),
                    )
                    for row in response.rows
                ]
        except Exception as e:
            if "attribute not found" in str(e) or "was not found" in str(e):
                return []
            raise
        return []

    async def _reconcile_observation(self, handle: str, obs: Observation) -> None:
        """Reconcile a single new observation against existing similar ones in turbopuffer."""
        embedding = await self._get_embedding(obs.content)
        similar = await self._find_similar_observations(handle, embedding, top_k=3)

        if not similar:
            # nothing similar — just add
            await self._write_observation(handle, obs, embedding)
            logger.info(f"ADD (no similar) for @{handle}: {obs.content[:60]}")
            return

        # ask the LLM to reconcile against the most similar existing observation
        best_match = similar[0]
        prompt = (
            f"EXISTING observation: {best_match['content']}\n"
            f"EXISTING tags: {best_match['tags']}\n\n"
            f"NEW observation: {obs.content}\n"
            f"NEW tags: {obs.tags}"
        )
        result = await get_reconciliation_agent().run(prompt)
        decision = result.output.decision
        action = decision.action.upper()

        user_ns = self.get_user_namespace(handle)

        if action == "ADD":
            await self._write_observation(handle, obs, embedding)
            logger.info(f"ADD for @{handle}: {obs.content[:60]} ({decision.reason})")

        elif action == "UPDATE":
            # mark old row superseded, write merged version linking back.
            # union sources so the new row inherits the full pedigree —
            # both the old observation's evidence and the new observation's.
            old_id = best_match["id"]
            user_ns.write(
                patch_rows=[{"id": old_id, "status": "superseded"}],
            )
            merged = Observation(
                content=decision.new_content or obs.content,
                tags=decision.new_tags or obs.tags,
                source_uris=obs.source_uris,
            )
            merged_embedding = await self._get_embedding(merged.content)
            unioned = list(
                dict.fromkeys(best_match.get("source_uris", []) + list(obs.source_uris))
            )
            await self._write_observation(
                handle,
                merged,
                merged_embedding,
                supersedes=old_id,
                source_uris_override=unioned,
            )
            logger.info(
                f"UPDATE for @{handle}: '{best_match['content'][:40]}' -> '{merged.content[:40]}' ({decision.reason})"
            )

        elif action == "DELETE":
            # mark old row superseded, write new one linking back.
            # don't union here — the new claim is asserting the old was
            # wrong, not refining it. preserve pedigree via supersedes link
            # for trace, but the new row stands on its own sources.
            old_id = best_match["id"]
            user_ns.write(
                patch_rows=[{"id": old_id, "status": "superseded"}],
            )
            await self._write_observation(handle, obs, embedding, supersedes=old_id)
            logger.info(
                f"DELETE+ADD for @{handle}: superseded '{best_match['content'][:40]}', added '{obs.content[:40]}' ({decision.reason})"
            )

        elif action == "NOOP":
            logger.debug(
                f"NOOP for @{handle}: '{obs.content[:60]}' ({decision.reason})"
            )

        else:
            # unknown action — fall back to ADD
            await self._write_observation(handle, obs, embedding)
            logger.warning(
                f"unknown reconciliation action '{action}' for @{handle}, falling back to ADD"
            )

    async def _write_observation(
        self,
        handle: str,
        obs: Observation,
        embedding: list[float],
        supersedes: str | None = None,
        source_uris_override: list[str] | None = None,
    ) -> None:
        """Write a single observation to turbopuffer.

        source_uris_override lets reconciliation (UPDATE) merge in URIs from
        the superseded row. Default uses obs.source_uris as-is.
        """
        user_ns = self.get_user_namespace(handle)
        entry_id = self._generate_id(f"user-{handle}", "observation", obs.content)
        now = datetime.now().isoformat()
        sources = (
            source_uris_override
            if source_uris_override is not None
            else list(obs.source_uris)
        )
        user_ns.write(
            upsert_rows=[
                {
                    "id": entry_id,
                    "vector": embedding,
                    "kind": "observation",
                    "status": "active",
                    "content": obs.content,
                    "tags": obs.tags,
                    "supersedes": supersedes or "",
                    "source_uris": sources,
                    "created_at": now,
                    "updated_at": now,
                }
            ],
            distance_metric="cosine_distance",
            schema=USER_NAMESPACE_SCHEMA,
        )

    async def get_relationship_summary(self, handle: str) -> str | None:
        """Get the compacted relationship summary for a user, if one exists."""
        user_ns = self.get_user_namespace(handle)
        try:
            response = user_ns.query(
                rank_by=("created_at", "desc"),
                top_k=1,
                filters={"kind": ["Eq", "summary"]},
                include_attributes=["content"],
            )
            if response.rows:
                return response.rows[0].content
        except Exception as e:
            if "not found" not in str(e).lower():
                logger.warning(
                    f"failed to fetch relationship summary for @{handle}: {e}"
                )
        return None

    async def build_user_context(self, handle: str, query_text: str) -> str:
        """Build context for a conversation from observations and recent interactions."""
        parts = []

        # relationship summary (synthesized by compact flow — treat as phi's impression, not ground truth)
        summary = await self.get_relationship_summary(handle)
        if summary:
            parts.append(
                f"\n[PHI'S SYNTHESIZED IMPRESSION OF @{handle} — trust: low, may contain hallucinations]"
            )
            parts.append(summary)

        user_ns = self.get_user_namespace(handle)
        try:
            query_embedding = await self._get_embedding(query_text)

            observations: list[ObservationRow] = []
            interactions: list[_InteractionDisplay] = []

            try:
                # semantic search for relevant observations (exclude superseded).
                # include_attributes=True (all) so we don't error on namespaces
                # whose schema predates source_uris — turbopuffer 400s if you
                # list an attribute the namespace doesn't know yet.
                obs_response = user_ns.query(
                    rank_by=("vector", "ANN", query_embedding),
                    top_k=10,
                    filters=[
                        "And",
                        [
                            ["kind", "Eq", "observation"],
                            ["status", "NotEq", "superseded"],
                        ],
                    ],
                    include_attributes=True,
                )
                if obs_response.rows:
                    observations = [
                        ObservationRow(
                            id=row.id,
                            content=row.content,
                            tags=getattr(row, "tags", []),
                            created_at=getattr(row, "created_at", "") or "",
                            source_uris=list(getattr(row, "source_uris", []) or []),
                        )
                        for row in obs_response.rows
                    ]

                # recent interactions for conversational context
                interaction_response = user_ns.query(
                    rank_by=("vector", "ANN", query_embedding),
                    top_k=5,
                    filters={"kind": ["Eq", "interaction"]},
                    include_attributes=True,
                )
                if interaction_response.rows:
                    interactions = [
                        _InteractionDisplay(
                            content=row.content,
                            created_at=getattr(row, "created_at", "") or "",
                        )
                        for row in interaction_response.rows
                    ]
            except Exception as e:
                if "attribute not found" not in str(e):
                    raise
                # old namespace without kind column - fall back to unfiltered search
                logger.debug(
                    f"kind attribute not found for @{handle}, falling back to unfiltered search"
                )
                response = user_ns.query(
                    rank_by=("vector", "ANN", query_embedding),
                    top_k=10,
                    include_attributes=["content"],
                )
                if response.rows:
                    interactions = [
                        _InteractionDisplay(content=row.content, created_at="")
                        for row in response.rows
                    ]

            # exploration notes (background research)
            exploration_notes: list[str] = []
            try:
                exp_response = user_ns.query(
                    rank_by=("vector", "ANN", query_embedding),
                    top_k=5,
                    filters=[
                        "And",
                        [
                            ["kind", "Eq", "exploration_note"],
                            ["status", "NotEq", "superseded"],
                        ],
                    ],
                    include_attributes=["content"],
                )
                if exp_response.rows:
                    exploration_notes = [row.content for row in exp_response.rows]
            except Exception:
                pass  # no exploration notes yet

            if observations:
                parts.append(
                    f"\n[OBSERVATIONS ABOUT @{handle} — extracted from user's own words, trust: medium. tail shows source count and age (uncited and/or aged observations are lower-trust).]"
                )
                for obs in observations:
                    parts.append(
                        f"- {obs['content']}"
                        f"{_citation_tail(obs['source_uris'], obs['created_at'])}"
                    )

            if interactions:
                parts.append(
                    f"\n[PAST EXCHANGES WITH @{handle} — verbatim logs, trust: high. age in parens.]"
                )
                for interaction in interactions:
                    age = relative_when(interaction["created_at"])
                    age_part = f" ({age})" if age else ""
                    parts.append(f"- {interaction['content']}{age_part}")

            if exploration_notes:
                parts.append(
                    f"\n[BACKGROUND RESEARCH ON @{handle} — phi explored their public activity, trust: lowest]"
                )
                for note in exploration_notes:
                    parts.append(f"- {note}")

            if not observations and not interactions and not exploration_notes:
                parts.append(f"\n[USER CONTEXT - @{handle}]")
                parts.append("no previous interactions with this user.")

        except Exception as e:
            if "was not found" not in str(e):
                logger.warning(f"failed to retrieve user context for @{handle}: {e}")
            parts.append(f"\n[USER CONTEXT - @{handle}]")
            parts.append("no previous interactions with this user.")

        return "\n".join(parts)

    async def search(self, handle: str, query: str, top_k: int = 10) -> list[dict]:
        """Unfiltered semantic search across all memory kinds for a user."""
        user_ns = self.get_user_namespace(handle)
        try:
            query_embedding = await self._get_embedding(query)
            response = user_ns.query(
                rank_by=("vector", "ANN", query_embedding),
                top_k=top_k,
                include_attributes=["content", "created_at"],
            )
            results = []
            if response.rows:
                for row in response.rows:
                    results.append(
                        {
                            "kind": getattr(row, "kind", "unknown"),
                            "content": row.content,
                            "tags": getattr(row, "tags", []),
                            "created_at": getattr(row, "created_at", ""),
                        }
                    )
            return results
        except Exception as e:
            if "was not found" in str(e):
                return []
            raise

    # --- episodic memory (phi's own world knowledge) ---

    async def store_episodic_memory(
        self,
        content: str,
        tags: list[str],
        source: str = "tool",
        source_uris: list[str] | None = None,
    ):
        """Store an episodic memory — something phi learned about the world.

        source_uris are AT-URIs that back this memory (a post phi was reading,
        a thread phi was in, a card phi made). Empty allowed but lower-trust
        on read.
        """
        entry_id = self._generate_id("episodic", source, content)
        self.namespaces["episodic"].write(
            upsert_rows=[
                {
                    "id": entry_id,
                    "vector": await self._get_embedding(content),
                    "content": content,
                    "tags": tags,
                    "source": source,
                    "source_uris": list(source_uris or []),
                    "created_at": datetime.now().isoformat(),
                }
            ],
            distance_metric="cosine_distance",
            schema=EPISODIC_SCHEMA,
        )
        logger.info(f"stored episodic memory [{source}]: {content[:80]}")

    async def search_episodic(self, query: str, top_k: int = 10) -> list[dict]:
        """Semantic search over phi's episodic memories."""
        try:
            query_embedding = await self._get_embedding(query)
            response = self.namespaces["episodic"].query(
                rank_by=("vector", "ANN", query_embedding),
                top_k=top_k,
                include_attributes=["content", "tags", "source", "created_at"],
            )
            results = []
            if response.rows:
                for row in response.rows:
                    results.append(
                        {
                            "content": row.content,
                            "tags": getattr(row, "tags", []),
                            "source": getattr(row, "source", "unknown"),
                            "created_at": getattr(row, "created_at", ""),
                        }
                    )
            return results
        except Exception as e:
            if "was not found" in str(e):
                return []
            raise

    async def get_episodic_context(
        self,
        query_text: str,
        goals: list[dict] | None = None,
        top_k: int = 10,
    ) -> str:
        """Get a haiku-synthesized episodic context block for the prompt.

        Top-K from the vector store, then a synthesis pass that takes phi's
        goals + the current query as context and produces a coherent
        block (deduped, recency-aware, contradictions flagged) instead of
        a raw dump of similarity-ranked notes.

        Returns an empty string if there are no relevant candidates.
        """
        raw = await self.search_episodic(query_text, top_k=top_k)
        if not raw:
            return ""
        summary = await _synthesize_episodic(goals or [], query_text, raw)
        if not summary:
            return ""
        return f"[RELEVANT MEMORIES — synthesized for this query]\n{summary}"

    async def search_unified(
        self, handle: str, query: str, top_k: int = 8
    ) -> list[dict]:
        """Search both user namespace and episodic namespace concurrently."""
        query_embedding = await self._get_embedding(query)

        user_ns = self.get_user_namespace(handle)
        loop = asyncio.get_event_loop()

        async def _search_user() -> list[dict]:
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda: user_ns.query(
                        rank_by=("vector", "ANN", query_embedding),
                        top_k=top_k,
                        include_attributes=["content", "kind", "tags", "created_at"],
                    ),
                )
                results = []
                if response.rows:
                    for row in response.rows:
                        results.append(
                            {
                                "content": row.content,
                                "kind": getattr(row, "kind", "unknown"),
                                "tags": getattr(row, "tags", []),
                                "created_at": getattr(row, "created_at", ""),
                                "_source": "user",
                            }
                        )
                return results
            except Exception as e:
                if "was not found" in str(e):
                    return []
                logger.warning(
                    f"unified search user namespace failed for @{handle}: {e}"
                )
                return []

        async def _search_episodic() -> list[dict]:
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda: self.namespaces["episodic"].query(
                        rank_by=("vector", "ANN", query_embedding),
                        top_k=top_k,
                        include_attributes=["content", "tags", "source", "created_at"],
                    ),
                )
                results = []
                if response.rows:
                    for row in response.rows:
                        results.append(
                            {
                                "content": row.content,
                                "tags": getattr(row, "tags", []),
                                "source": getattr(row, "source", "unknown"),
                                "created_at": getattr(row, "created_at", ""),
                                "_source": "episodic",
                            }
                        )
                return results
            except Exception as e:
                if "was not found" in str(e):
                    return []
                logger.warning(f"unified search episodic namespace failed: {e}")
                return []

        user_results, episodic_results = await asyncio.gather(
            _search_user(), _search_episodic()
        )
        return user_results + episodic_results

    @staticmethod
    def _project_2d(
        centroids: dict[str, list[float]],
    ) -> dict[str, tuple[float, float]]:
        """Project high-dimensional centroids to 2D via PCA (top 2 principal components)."""
        import numpy as np

        if len(centroids) < 2:
            return {nid: (0.0, 0.0) for nid in centroids}

        ids = list(centroids.keys())
        X = np.array([centroids[nid] for nid in ids])
        X -= X.mean(axis=0)

        # SVD on centered data — U[:, :2] * S[:2] gives the top-2 PC projections
        U, S, _ = np.linalg.svd(X, full_matrices=False)
        proj = U[:, :2] * S[:2]

        # normalize to [-1, 1]
        for col in range(2):
            lo, hi = proj[:, col].min(), proj[:, col].max()
            span = hi - lo or 1.0
            proj[:, col] = 2 * (proj[:, col] - lo) / span - 1

        return {
            nid: (float(proj[i, 0]), float(proj[i, 1])) for i, nid in enumerate(ids)
        }

    def get_graph_data(self) -> dict:
        """Build graph nodes and edges from memory namespaces with semantic coordinates."""
        nodes = [{"id": "phi", "label": "phi", "type": "phi"}]
        edges = []
        user_vectors: dict[str, list[list[float]]] = {}

        # discover user namespaces
        user_prefix = f"{self.NAMESPACES['users']}-"
        try:
            page = self.client.namespaces(prefix=user_prefix)
            for ns_summary in page.namespaces:
                handle = ns_summary.id.removeprefix(user_prefix).replace("_", ".")
                nodes.append(
                    {"id": f"user:{handle}", "label": f"@{handle}", "type": "user"}
                )
                edges.append({"source": "phi", "target": f"user:{handle}"})

                # get observation vectors for semantic positioning
                user_ns = self.client.namespace(ns_summary.id)
                try:
                    response = user_ns.query(
                        rank_by=("vector", "ANN", [0.5] * 1536),
                        top_k=50,
                        filters=[
                            "And",
                            [
                                ["kind", "Eq", "observation"],
                                ["status", "NotEq", "superseded"],
                            ],
                        ],
                        include_attributes=["vector"],
                    )
                    if response.rows:
                        for row in response.rows:
                            vec = getattr(row, "vector", None)
                            if vec:
                                user_vectors.setdefault(handle, []).append(vec)
                except Exception:
                    pass  # old namespace or no observations
        except Exception as e:
            logger.warning(f"failed to list user namespaces: {e}")

        # compute per-node embedding centroids
        def _centroid(vecs: list[list[float]]) -> list[float]:
            n = len(vecs)
            dim = len(vecs[0])
            return [sum(v[i] for v in vecs) / n for i in range(dim)]

        centroids: dict[str, list[float]] = {}
        for handle, vecs in user_vectors.items():
            centroids[f"user:{handle}"] = _centroid(vecs)

        coords = self._project_2d(centroids)

        for node in nodes:
            nid = node["id"]
            if nid == "phi":
                node["x"] = 0.0
                node["y"] = 0.0
            elif nid in coords:
                node["x"] = round(coords[nid][0], 4)
                node["y"] = round(coords[nid][1], 4)
            else:
                node["x"] = None
                node["y"] = None

        return {"nodes": nodes, "edges": edges}

    async def get_recent_interactions(self, top_k: int = 10) -> list[dict]:
        """Get recent interactions across all user namespaces for reflection."""
        user_prefix = f"{self.NAMESPACES['users']}-"
        results: list[dict] = []
        try:
            page = self.client.namespaces(prefix=user_prefix)
            for ns_summary in page.namespaces:
                handle = ns_summary.id.removeprefix(user_prefix).replace("_", ".")
                user_ns = self.client.namespace(ns_summary.id)
                try:
                    response = user_ns.query(
                        rank_by=("created_at", "desc"),
                        top_k=3,
                        filters={"kind": ["Eq", "interaction"]},
                        include_attributes=["content", "created_at"],
                    )
                    if response.rows:
                        for row in response.rows:
                            results.append(
                                {
                                    "handle": handle,
                                    "content": row.content,
                                    "created_at": getattr(row, "created_at", ""),
                                }
                            )
                except Exception:
                    pass  # old namespace or no interactions
        except Exception as e:
            logger.warning(f"failed to list user namespaces for reflection: {e}")

        # sort by created_at descending, take top_k
        results.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return results[:top_k]

    async def get_unprocessed_interactions(
        self, top_k: int = 20
    ) -> list[InteractionRow]:
        """Get recent interactions that haven't been reviewed for observation extraction.

        Uses a timestamp heuristic: interactions newer than the most recent
        observation in each user namespace are considered unprocessed.
        """
        user_prefix = f"{self.NAMESPACES['users']}-"
        results: list[InteractionRow] = []
        try:
            page = self.client.namespaces(prefix=user_prefix)
            for ns_summary in page.namespaces:
                handle = ns_summary.id.removeprefix(user_prefix).replace("_", ".")
                user_ns = self.client.namespace(ns_summary.id)

                # find the latest observation timestamp
                latest_obs_time = ""
                try:
                    obs_response = user_ns.query(
                        rank_by=("created_at", "desc"),
                        top_k=1,
                        filters=[
                            "And",
                            [
                                ["kind", "Eq", "observation"],
                                ["status", "NotEq", "superseded"],
                            ],
                        ],
                        include_attributes=["created_at"],
                    )
                    if obs_response.rows:
                        latest_obs_time = (
                            getattr(obs_response.rows[0], "created_at", "") or ""
                        )
                except Exception:
                    pass

                # get interactions newer than that
                try:
                    int_response = user_ns.query(
                        rank_by=("created_at", "desc"),
                        top_k=5,
                        filters={"kind": ["Eq", "interaction"]},
                        include_attributes=True,
                    )
                    if int_response.rows:
                        for row in int_response.rows:
                            created = getattr(row, "created_at", "") or ""
                            if created > latest_obs_time:
                                results.append(
                                    InteractionRow(
                                        handle=handle,
                                        content=row.content,
                                        created_at=created,
                                        source_uris=list(
                                            getattr(row, "source_uris", []) or []
                                        ),
                                    )
                                )
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"failed to get unprocessed interactions: {e}")

        results.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return results[:top_k]

    async def get_knowledge_count(self, handle: str) -> int:
        """Count observations + exploration notes phi has stored about a handle.

        Used to gauge how much we know about someone — for exploration triggers
        and pre-reply lookup decisions.
        """
        user_ns = self.get_user_namespace(handle)
        try:
            response = user_ns.query(
                rank_by=("created_at", "desc"),
                top_k=2,
                filters=[
                    "And",
                    [
                        ["kind", "In", ["observation", "exploration_note"]],
                        ["status", "NotEq", "superseded"],
                    ],
                ],
                include_attributes=["kind"],
            )
            return len(response.rows) if response.rows else 0
        except Exception:
            return 0  # namespace may not exist yet — treated as stranger

    async def is_stranger(self, handle: str) -> bool:
        """True if phi has fewer than 2 stored knowledge items about this handle."""
        return await self.get_knowledge_count(handle) < 2

    async def after_interaction(
        self,
        handle: str,
        user_text: str,
        bot_text: str,
        source_uris: list[str] | None = None,
    ):
        """Post-interaction hook: store the raw exchange with source URIs."""
        await self.store_interaction(
            handle, user_text, bot_text, source_uris=source_uris
        )
