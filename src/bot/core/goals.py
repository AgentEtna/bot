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
    progress_signal: str,
    kind: str = "goal",
) -> str:
    """Create (rkey=None) or update a goal's CONSTITUTIONAL fields. Returns AT-URI.

    On update, the operational fields written by update_goal_progress
    (current_state / next_step / last_step / last_step_at / blocked_by) are
    preserved — this only touches title / description / progress_signal / kind.
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
            progress_signal=progress_signal,
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
            "progress_signal": progress_signal,
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
) -> str:
    """Update a goal's OPERATIONAL fields, preserving its constitutional ones.

    Not owner-gated at the tool layer — phi keeps its own goals honest (what
    it just did, where things stand, the next concrete step). Sets
    last_step_at to now so the [GOALS AND INTERESTS] block can show staleness.
    """
    await client.authenticate()
    assert client.client.me is not None
    existing = await get_goal(client, rkey)
    if existing is None:
        raise ValueError(f"no goal with rkey {rkey!r}")
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
    result = client.client.com.atproto.repo.put_record(
        data={
            "repo": client.client.me.did,
            "collection": GOAL_COLLECTION,
            "rkey": rkey,
            "record": record,
        }
    )
    return result.uri
