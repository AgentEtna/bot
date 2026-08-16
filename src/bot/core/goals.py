"""Phi's goals — durable anchors stored on PDS.

Each goal is a single record under `io.zzstoatzz.phi.goal`. Updates use
putRecord with the same rkey, so the goal evolves in place over time.
History is in the firehose if anyone wants it.

Mutation is gated at the tool layer (propose_goal_change) using the same
_is_owner check as follow_user — so phi can only change its own goals
when the owner has just liked an authorization-request post.
"""

from datetime import UTC, datetime

from bot.core.atproto_client import BotClient

GOAL_COLLECTION = "io.zzstoatzz.phi.goal"

# Hard caps for the phi-writable goal fields. These are states and steps —
# on 2026-08-07 the chicken goal's `current` was a 1,600-char trading
# journal (per-position marks, EV math, doctrine citations) rendered into
# every run's context. The reasoning belongs in a greengale report or a
# memory; the field carries the conclusion. Enforced at the tool
# (update_goal_progress rejects over-cap writes) and clamped at render for
# values written before the cap existed.
FIELD_CAPS = {
    "current_state": 400,
    "next_step": 250,
    "last_step": 250,
    "progress_signal": 300,
}


async def list_goals(client: BotClient) -> list[dict]:
    """Read all goal records from phi's PDS. Each result includes _rkey."""
    await client.authenticate()
    if not client.client.me:
        return []
    try:
        response = client.client.com.atproto.repo.list_records(
            {
                "repo": client.client.me.did,
                "collection": GOAL_COLLECTION,
                "limit": 20,
            }
        )
        out = []
        for rec in response.records:
            value = dict(rec.value)
            value["_rkey"] = rec.uri.split("/")[-1]
            out.append(value)
        return out
    except Exception:
        return []


async def get_goal(client: BotClient, rkey: str) -> dict | None:
    await client.authenticate()
    if not client.client.me:
        return None
    try:
        response = client.client.com.atproto.repo.get_record(
            params={
                "repo": client.client.me.did,
                "collection": GOAL_COLLECTION,
                "rkey": rkey,
            }
        )
        return dict(response.value) if response.value else None
    except Exception:
        return None


async def upsert_goal(
    client: BotClient,
    rkey: str | None,
    title: str,
    description: str,
    metabolism: str,
    kind: str = "goal",
) -> str:
    """Create (rkey=None) or update a goal's CONSTITUTIONAL fields. Returns AT-URI.

    Constitutional = direction: title / description / metabolism / kind.
    The measure (progress_signal) is phi-owned and lives with the
    operational fields — the operator gates what phi is FOR, never the
    scorekeeping (a hand-written operator measure went stale for three
    months once; see docs/goals-metabolism.md).

    On update, the fields written by update_goal_progress (current_state /
    next_step / last_step / last_step_at / blocked_by / progress_signal)
    are preserved.
    """
    if kind not in ("goal", "interest"):
        raise ValueError(f"kind must be 'goal' or 'interest', got {kind!r}")
    await client.authenticate()
    assert client.client.me is not None
    now = datetime.now(UTC).isoformat()

    if rkey:
        existing = await get_goal(client, rkey)
        record: dict = dict(existing) if existing else {}
        record.pop("_rkey", None)
        record.update(
            title=title,
            description=description,
            metabolism=metabolism,
            kind=kind,
            updated_at=now,
        )
        record.setdefault("created_at", now)
        result = client.client.com.atproto.repo.put_record(
            data={
                "repo": client.client.me.did,
                "collection": GOAL_COLLECTION,
                "rkey": rkey,
                "record": record,
            }
        )
    else:
        record = {
            "title": title,
            "description": description,
            "metabolism": metabolism,
            "kind": kind,
            "created_at": now,
            "updated_at": now,
        }
        result = client.client.com.atproto.repo.create_record(
            data={
                "repo": client.client.me.did,
                "collection": GOAL_COLLECTION,
                "record": record,
            }
        )
    return result.uri


async def update_goal_progress(
    client: BotClient,
    rkey: str,
    current_state: str,
    next_step: str,
    last_step: str,
    blocked_by: str = "",
    evidence_uris: list[str] | None = None,
    progress_signal: str | None = None,
) -> str | None:
    """Update a goal's OPERATIONAL fields, preserving its constitutional ones.

    Not owner-gated at the tool layer — phi keeps its own goals honest (what
    it just did, where things stand, the next concrete step). Sets
    last_step_at to now so the [GOALS AND INTERESTS] block can show staleness.

    Returns None without writing when current_state and next_step match the
    stored record — re-recording an unchanged state is journaling, and the
    journal is episodic memory (run summaries land there unconditionally).
    """
    await client.authenticate()
    assert client.client.me is not None
    existing = await get_goal(client, rkey)
    if existing is None:
        raise ValueError(f"no goal with rkey {rkey!r}")
    if (
        existing.get("current_state") == current_state
        and existing.get("next_step") == next_step
    ):
        return None
    now = datetime.now(UTC).isoformat()
    record = dict(existing)
    record.pop("_rkey", None)
    record.update(
        current_state=current_state,
        next_step=next_step,
        last_step=last_step,
        last_step_at=now,
        blocked_by=blocked_by,
        updated_at=now,
    )
    if evidence_uris:
        record["evidence_uris"] = evidence_uris
    if progress_signal is not None:
        record["progress_signal"] = progress_signal
    result = client.client.com.atproto.repo.put_record(
        data={
            "repo": client.client.me.did,
            "collection": GOAL_COLLECTION,
            "rkey": rkey,
            "record": record,
        }
    )
    return result.uri
