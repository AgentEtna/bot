"""Read-only audit of phi's state across PDS + turbopuffer.

Surveys what's currently stored:
- PDS: network.cosmik.card, network.cosmik.connection
- tpuf: phi-users-* namespaces (per-kind row counts), phi-episodic

Read-only. Touches no records. Run from the bot/ directory:

    uv run python scripts/audit_state.py
"""

from collections import Counter

import turbopuffer
from atproto import Client
from atproto_client.models.utils import get_model_as_dict

from bot.config import settings


def audit_pds() -> dict:
    """Walk phi's PDS collections, returning per-collection summaries."""
    client = Client(base_url=settings.bluesky_service)
    client.login(settings.bluesky_handle, settings.bluesky_password)
    did = client.me.did

    def list_all(collection: str) -> list[dict]:
        """Return [{uri, cid, value}] with `value` as a plain dict."""
        records: list[dict] = []
        cursor = None
        while True:
            params = {"repo": did, "collection": collection, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            resp = client.com.atproto.repo.list_records(params)
            for r in resp.records or []:
                records.append(
                    {
                        "uri": r.uri,
                        "cid": r.cid,
                        "value": get_model_as_dict(r.value) if r.value else {},
                    }
                )
            cursor = getattr(resp, "cursor", None)
            if not cursor:
                break
        return records

    out: dict = {"did": did}

    # network.cosmik.card — destination layer
    cards = list_all("network.cosmik.card")
    note_with_parent = 0
    note_orphan = 0
    url_cards = 0
    other_type = 0
    for r in cards:
        v = r["value"]
        t = v.get("type", "")
        if t == "NOTE":
            if v.get("parentCard"):
                note_with_parent += 1
            else:
                note_orphan += 1
        elif t == "URL":
            url_cards += 1
        else:
            other_type += 1
    out["card"] = {
        "count": len(cards),
        "NOTE_with_parentCard": note_with_parent,
        "NOTE_orphan": note_orphan,
        "URL": url_cards,
        "other_type": other_type,
    }

    # network.cosmik.connection — check schema conformance (source/target vs from/to)
    conns = list_all("network.cosmik.connection")
    well_formed = 0
    malformed_from_to = 0
    type_counter: Counter[str] = Counter()
    for r in conns:
        v = r["value"]
        has_source_target = bool(v.get("source") and v.get("target"))
        has_from_to = bool(v.get("from") and v.get("to"))
        if has_source_target:
            well_formed += 1
            type_counter[v.get("connectionType") or "<unset>"] += 1
        elif has_from_to:
            malformed_from_to += 1
            type_counter[f"MALFORMED:{v.get('type') or '<unset>'}"] += 1
        else:
            malformed_from_to += 1
            type_counter["MALFORMED:no_endpoints"] += 1
    out["connection"] = {
        "count": len(conns),
        "well_formed_source_target": well_formed,
        "malformed_from_to": malformed_from_to,
        "by_type": dict(type_counter),
    }

    return out


def audit_tpuf() -> dict:
    """Walk every phi-* namespace and report per-kind row counts."""
    if not settings.turbopuffer_api_key:
        return {"error": "no TURBOPUFFER_API_KEY"}

    client = turbopuffer.Turbopuffer(
        api_key=settings.turbopuffer_api_key, region=settings.turbopuffer_region
    )
    out: dict = {"namespaces": {}}

    # Find every namespace phi cares about
    user_ns_ids: list[str] = []
    page = client.namespaces(prefix="phi-users-")
    for ns_summary in page.namespaces:
        user_ns_ids.append(ns_summary.id)

    # Per-user namespaces: count by kind + by status
    EMBEDDING_DIM = 1536
    for ns_id in user_ns_ids:
        ns = client.namespace(ns_id)
        kinds_total: Counter[str] = Counter()
        status_total: Counter[str] = Counter()
        try:
            resp = ns.query(
                rank_by=("vector", "ANN", [0.5] * EMBEDDING_DIM),
                top_k=2000,
                include_attributes=True,
            )
            for row in resp.rows or []:
                kinds_total[getattr(row, "kind", None) or "<missing>"] += 1
                status_total[getattr(row, "status", None) or "<missing>"] += 1
        except Exception as e:
            out["namespaces"][ns_id] = {"error": str(e)}
            continue
        out["namespaces"][ns_id] = {
            "by_kind": dict(kinds_total),
            "by_status": dict(status_total),
            "total": sum(kinds_total.values()),
        }

    # phi-episodic
    for shared in ("phi-episodic",):
        ns = client.namespace(shared)
        try:
            resp = ns.query(
                rank_by=("vector", "ANN", [0.5] * EMBEDDING_DIM),
                top_k=2000,
                include_attributes=True,
            )
            kinds: Counter[str] = Counter()
            for row in resp.rows or []:
                key = (
                    getattr(row, "source", None)
                    or getattr(row, "archival_reason", None)
                    or "<unlabeled>"
                )
                kinds[key] += 1
            out["namespaces"][shared] = {
                "by_label": dict(kinds),
                "total": sum(kinds.values()),
            }
        except Exception as e:
            out["namespaces"][shared] = {"error": str(e)}

    return out


def print_summary(pds: dict, tpuf: dict) -> None:
    print("\n=== PDS ===")
    print(f"did: {pds['did']}\n")

    c = pds["card"]
    print("network.cosmik.card:")
    print(f"  count: {c['count']}")
    print(f"    NOTE (with parentCard): {c['NOTE_with_parentCard']}")
    print(f"    NOTE (orphan, semble drops): {c['NOTE_orphan']}")
    print(f"    URL: {c['URL']}")
    print(f"    other/unknown type: {c['other_type']}")
    print()

    cn = pds["connection"]
    print("network.cosmik.connection:")
    print(f"  count: {cn['count']}")
    print(f"    well-formed (source/target): {cn['well_formed_source_target']}")
    print(f"    malformed (from/to or missing): {cn['malformed_from_to']}")
    if cn["by_type"]:
        print("    by type:")
        for k, n in sorted(cn["by_type"].items(), key=lambda kv: -kv[1]):
            print(f"      {k}: {n}")
    print()

    print("=== TPUF ===")
    if tpuf.get("error"):
        print(f"  {tpuf['error']}")
        return

    ns_map = tpuf["namespaces"]
    user_ns_ids = sorted([k for k in ns_map if k.startswith("phi-users-")])
    print(f"\n{len(user_ns_ids)} phi-users-* namespaces\n")

    grand_total: Counter[str] = Counter()
    for ns_id in user_ns_ids:
        info = ns_map[ns_id]
        if "error" in info:
            print(f"  {ns_id}: ERROR {info['error']}")
            continue
        print(f"  {ns_id}: {info['total']} rows")
        for k, n in sorted(info["by_kind"].items(), key=lambda kv: -kv[1]):
            print(f"    {k}: {n}")
            grand_total[k] += n
        if info["by_status"]:
            status_parts = ", ".join(
                f"{s}={n}" for s, n in sorted(info["by_status"].items())
            )
            print(f"    status: {status_parts}")

    if grand_total:
        print("\n  GRAND TOTAL (all phi-users-* namespaces):")
        for k, n in sorted(grand_total.items(), key=lambda kv: -kv[1]):
            print(f"    {k}: {n}")

    for shared in ("phi-episodic",):
        info = ns_map.get(shared)
        if not info:
            continue
        print(f"\n  {shared}: {info.get('total', '?')} rows")
        if "error" in info:
            print(f"    error: {info['error']}")
            continue
        for k, n in sorted(info["by_label"].items(), key=lambda kv: -kv[1]):
            print(f"    {k}: {n}")


def main():
    print("auditing phi state — read-only…")
    pds = audit_pds()
    tpuf = audit_tpuf()
    print_summary(pds, tpuf)


if __name__ == "__main__":
    main()
