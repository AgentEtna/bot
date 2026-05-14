"""Inspect and prune stored memories.

Usage:
    uv run scripts/memory_inspect.py                          # list all user namespaces
    uv run scripts/memory_inspect.py USER_HANDLE              # dump observations + interactions for a user
    uv run scripts/memory_inspect.py USER_HANDLE --delete ID  # delete a specific row by ID
    uv run scripts/memory_inspect.py USER_HANDLE --purge-observations  # delete ALL observations for a user
    uv run scripts/memory_inspect.py --episodic               # dump phi's episodic memories
"""

import argparse

from turbopuffer import Turbopuffer

from bot.config import settings


def get_client() -> Turbopuffer:
    return Turbopuffer(
        api_key=settings.turbopuffer_api_key, region=settings.turbopuffer_region
    )


def list_namespaces(client: Turbopuffer):
    """List all namespaces that look like user memory."""
    prefix = "phi-users-"
    namespaces = client.namespaces()
    user_ns = [ns for ns in namespaces if ns.id.startswith(prefix)]
    if not user_ns:
        print("no user namespaces found")
        return
    print(f"found {len(user_ns)} user namespaces:\n")
    for ns in sorted(user_ns, key=lambda n: n.id):
        handle = ns.id.removeprefix(prefix).replace("_", ".")
        print(f"  {handle:<40} ({ns.id})")


def dump_user(client: Turbopuffer, handle: str):
    """Dump all memory for a user."""
    clean = handle.replace(".", "_").replace("@", "").replace("-", "_")
    ns_name = f"phi-users-{clean}"
    ns = client.namespace(ns_name)

    try:
        response = ns.query(
            rank_by=("vector", "ANN", [0.5] * 1536),
            top_k=200,
            include_attributes=["kind", "content", "tags", "created_at"],
        )
    except Exception as e:
        if "was not found" in str(e):
            print(f"no namespace found for @{handle} ({ns_name})")
            return
        if "attribute" in str(e) and "not found" in str(e):
            # old namespace without kind/tags columns
            response = ns.query(
                rank_by=("vector", "ANN", [0.5] * 1536),
                top_k=200,
                include_attributes=True,
            )
        else:
            raise

    if not response.rows:
        print(f"no rows found for @{handle}")
        return

    observations = []
    interactions = []
    for row in response.rows:
        kind = getattr(row, "kind", "unknown")
        entry = {
            "id": row.id,
            "content": row.content,
            "tags": getattr(row, "tags", []),
            "created_at": getattr(row, "created_at", ""),
        }
        if kind == "observation":
            observations.append(entry)
        else:
            interactions.append(entry)

    if observations:
        print(f"=== observations ({len(observations)}) ===\n")
        for obs in observations:
            tags = f" [{', '.join(obs['tags'])}]" if obs["tags"] else ""
            print(f"  [{obs['id']}] {obs['content']}{tags}")
            if obs["created_at"]:
                print(f"    created: {obs['created_at']}")
            print()

    if interactions:
        print(f"=== interactions ({len(interactions)}) ===\n")
        for ix in interactions:
            content = ix["content"].replace("\n", "\n    ")
            print(f"  [{ix['id']}]")
            print(f"    {content}")
            if ix["created_at"]:
                print(f"    created: {ix['created_at']}")
            print()

    print(f"total: {len(observations)} observations, {len(interactions)} interactions")


def delete_row(client: Turbopuffer, handle: str, row_id: str):
    """Delete a specific row by ID."""
    clean = handle.replace(".", "_").replace("@", "").replace("-", "_")
    ns_name = f"phi-users-{clean}"
    ns = client.namespace(ns_name)
    ns.write(deletes=[row_id])
    print(f"deleted row {row_id} from {ns_name}")


def purge_observations(client: Turbopuffer, handle: str):
    """Delete all observations for a user."""
    clean = handle.replace(".", "_").replace("@", "").replace("-", "_")
    ns_name = f"phi-users-{clean}"
    ns = client.namespace(ns_name)

    try:
        response = ns.query(
            rank_by=("vector", "ANN", [0.5] * 1536),
            top_k=200,
            filters={"kind": ["Eq", "observation"]},
            include_attributes=["content"],
        )
    except Exception as e:
        if "was not found" in str(e):
            print(f"no namespace found for @{handle}")
            return
        raise

    if not response.rows:
        print(f"no observations to purge for @{handle}")
        return

    ids = [row.id for row in response.rows]
    print(f"purging {len(ids)} observations for @{handle}:")
    for row in response.rows:
        print(f"  - {row.content}")

    ns.write(deletes=ids)
    print(f"\ndeleted {len(ids)} observations")


def dump_episodic(client: Turbopuffer):
    """Dump phi's episodic memories."""
    ns = client.namespace("phi-episodic")

    try:
        response = ns.query(
            rank_by=("vector", "ANN", [0.5] * 1536),
            top_k=200,
            include_attributes=["content", "tags", "source", "created_at"],
        )
    except Exception as e:
        if "was not found" in str(e):
            print("no episodic memories found (namespace doesn't exist yet)")
            return
        raise

    if not response.rows:
        print("no episodic memories found")
        return

    print(f"=== episodic memories ({len(response.rows)}) ===\n")
    for row in response.rows:
        tags = getattr(row, "tags", [])
        source = getattr(row, "source", "unknown")
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        print(f"  [{row.id}] {row.content}{tag_str}")
        print(f"    source: {source}  created: {getattr(row, 'created_at', '')}")
        print()

    print(f"total: {len(response.rows)} episodic memories")


def main():
    parser = argparse.ArgumentParser(description="Inspect and prune phi memories")
    parser.add_argument("handle", nargs="?", help="User handle to inspect")
    parser.add_argument("--delete", metavar="ID", help="Delete a specific row by ID")
    parser.add_argument(
        "--purge-observations",
        action="store_true",
        help="Delete all observations for a user",
    )
    parser.add_argument(
        "--episodic", action="store_true", help="Dump phi's episodic (world) memories"
    )
    args = parser.parse_args()

    client = get_client()

    if args.episodic:
        dump_episodic(client)
        return

    if not args.handle:
        list_namespaces(client)
        return

    if args.purge_observations:
        purge_observations(client, args.handle)
    elif args.delete:
        delete_row(client, args.handle, args.delete)
    else:
        dump_user(client, args.handle)


if __name__ == "__main__":
    main()
