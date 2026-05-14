"""Show which bot version created each memory record.

Cross-references turbopuffer record timestamps against Fly.io deployment history
and git tags to attribute each record to a bot version.

Usage:
    uv run scripts/memory_versions.py                      # all user namespaces
    uv run scripts/memory_versions.py USER_HANDLE           # specific user
    uv run scripts/memory_versions.py --summary             # version counts only
    uv run scripts/memory_versions.py --episodic            # episodic memories
"""

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime

from turbopuffer import Turbopuffer

from bot.config import settings


def get_client() -> Turbopuffer:
    return Turbopuffer(
        api_key=settings.turbopuffer_api_key, region=settings.turbopuffer_region
    )


def get_deploy_windows() -> list[dict]:
    """Build version windows from Fly.io releases and git tags.

    Returns a sorted list of {start, end, fly_version, git_tag} dicts.
    """
    # fly.io releases
    result = subprocess.run(
        ["fly", "releases", "-a", "zzstoatzz-phi", "--json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"error fetching fly releases: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    releases = json.loads(result.stdout)
    releases.sort(key=lambda r: r["CreatedAt"])

    # git tags with timestamps
    result = subprocess.run(
        [
            "git",
            "tag",
            "-l",
            "v*",
            "--format=%(creatordate:iso-strict) %(refname:short)",
        ],
        capture_output=True,
        text=True,
        cwd=".",
    )
    tag_times: dict[str, datetime] = {}
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        # format: "2026-03-25T01:21:17-05:00 v0.0.8"
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            ts = datetime.fromisoformat(parts[0])
            tag_times[parts[1]] = ts

    # build windows: each release's window is [its start, next release's start)
    windows = []
    for i, rel in enumerate(releases):
        start = datetime.fromisoformat(rel["CreatedAt"].replace("Z", "+00:00"))
        if i + 1 < len(releases):
            end = datetime.fromisoformat(
                releases[i + 1]["CreatedAt"].replace("Z", "+00:00")
            )
        else:
            end = datetime.now(UTC)

        fly_version = rel["Version"]

        # find the most recent git tag at or before this deploy
        matching_tag = None
        for tag, tag_ts in sorted(
            tag_times.items(), key=lambda kv: kv[1], reverse=True
        ):
            tag_utc = tag_ts.astimezone(UTC)
            if tag_utc <= start:
                matching_tag = tag
                break

        windows.append(
            {
                "start": start,
                "end": end,
                "fly_version": fly_version,
                "git_tag": matching_tag or "pre-tags",
            }
        )

    return windows


def classify_record(created_at: str, windows: list[dict]) -> dict:
    """Find which deploy window a record's created_at falls into."""
    if not created_at:
        return {"fly_version": "?", "git_tag": "?"}

    try:
        ts = datetime.fromisoformat(created_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    except ValueError:
        return {"fly_version": "?", "git_tag": "?"}

    for w in windows:
        if w["start"] <= ts < w["end"]:
            return {"fly_version": w["fly_version"], "git_tag": w["git_tag"]}

    # before earliest deploy
    if windows and ts < windows[0]["start"]:
        return {"fly_version": f"<{windows[0]['fly_version']}", "git_tag": "pre-deploy"}

    return {"fly_version": "?", "git_tag": "?"}


def dump_with_versions(
    client: Turbopuffer, handle: str, windows: list[dict], summary_only: bool = False
):
    """Dump records for a user, annotated with bot version."""
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
            print(f"no namespace found for @{handle}")
            return
        if "attribute" in str(e) and "not found" in str(e):
            response = ns.query(
                rank_by=("vector", "ANN", [0.5] * 1536),
                top_k=200,
                include_attributes=True,
            )
        else:
            raise

    if not response.rows:
        print(f"no rows for @{handle}")
        return

    # classify each record
    records = []
    for row in response.rows:
        created_at = getattr(row, "created_at", "")
        version_info = classify_record(created_at, windows)
        records.append(
            {
                "id": row.id,
                "kind": getattr(row, "kind", "?"),
                "content": row.content,
                "tags": getattr(row, "tags", []),
                "created_at": created_at,
                **version_info,
            }
        )

    if summary_only:
        print(f"\n@{handle} ({len(records)} records)")
        counts: dict[str, dict[str, int]] = {}
        for r in records:
            label = r["git_tag"]
            kind = r["kind"]
            counts.setdefault(label, {}).setdefault(kind, 0)
            counts[label][kind] += 1
        for label in sorted(counts.keys()):
            kinds = ", ".join(f"{k}={v}" for k, v in sorted(counts[label].items()))
            print(f"  {label:<15} {kinds}")
        return

    print(f"\n{'=' * 70}")
    print(f"@{handle} ({len(records)} records)")
    print(f"{'=' * 70}\n")

    for r in sorted(records, key=lambda x: x["created_at"]):
        kind = r["kind"]
        content = r["content"][:90].replace("\n", " ")
        tags = f" [{', '.join(r['tags'])}]" if r["tags"] else ""
        version = f"{r['git_tag']} (fly v{r['fly_version']})"
        print(f"  {version:<25} ({kind:<11}) {content}{tags}")
        print(f"  {'':25} [{r['id']}] {r['created_at']}")
        print()


def dump_episodic_with_versions(
    client: Turbopuffer, windows: list[dict], summary_only: bool = False
):
    """Dump episodic memories annotated with bot version."""
    ns = client.namespace("phi-episodic")

    try:
        response = ns.query(
            rank_by=("vector", "ANN", [0.5] * 1536),
            top_k=200,
            include_attributes=["content", "tags", "source", "created_at"],
        )
    except Exception as e:
        if "was not found" in str(e):
            print("no episodic memories found")
            return
        raise

    if not response.rows:
        print("no episodic memories")
        return

    records = []
    for row in response.rows:
        created_at = getattr(row, "created_at", "")
        version_info = classify_record(created_at, windows)
        records.append(
            {
                "id": row.id,
                "content": row.content,
                "tags": getattr(row, "tags", []),
                "source": getattr(row, "source", "unknown"),
                "created_at": created_at,
                **version_info,
            }
        )

    if summary_only:
        print(f"\nepisodic ({len(records)} records)")
        counts: dict[str, int] = {}
        for r in records:
            counts[r["git_tag"]] = counts.get(r["git_tag"], 0) + 1
        for tag in sorted(counts.keys()):
            print(f"  {tag:<15} {counts[tag]} records")
        return

    print(f"\n{'=' * 60}")
    print(f"episodic memories ({len(records)} records)")
    print(f"{'=' * 60}\n")

    by_version: dict[str, list[dict]] = {}
    for r in records:
        by_version.setdefault(r["git_tag"], []).append(r)

    for tag in sorted(by_version.keys()):
        group = by_version[tag]
        print(f"--- {tag} (fly v{group[0]['fly_version']}) ---\n")
        for r in sorted(group, key=lambda x: x["created_at"]):
            content = r["content"][:100].replace("\n", " ")
            tags = f" [{', '.join(r['tags'])}]" if r["tags"] else ""
            print(f"  [{r['id']}] {content}{tags}")
            print(f"    source: {r['source']}  {r['created_at']}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Show which bot version created each memory"
    )
    parser.add_argument("handle", nargs="?", help="User handle to inspect")
    parser.add_argument("--summary", action="store_true", help="Version counts only")
    parser.add_argument("--episodic", action="store_true", help="Episodic memories")
    parser.add_argument("--all", action="store_true", help="All user namespaces")
    args = parser.parse_args()

    client = get_client()
    windows = get_deploy_windows()

    if args.episodic:
        dump_episodic_with_versions(client, windows, args.summary)
        return

    if args.handle:
        dump_with_versions(client, args.handle, windows, args.summary)
        return

    if args.all or args.summary:
        prefix = "phi-users-"
        page = client.namespaces(prefix=prefix)
        for ns_summary in sorted(page.namespaces, key=lambda n: n.id):
            handle = ns_summary.id.removeprefix(prefix).replace("_", ".")
            dump_with_versions(client, handle, windows, args.summary)
        return

    # default: list namespaces
    prefix = "phi-users-"
    page = client.namespaces(prefix=prefix)
    user_ns = [ns for ns in page.namespaces if ns.id.startswith(prefix)]
    if not user_ns:
        print("no user namespaces found")
        return
    print(f"found {len(user_ns)} user namespaces:\n")
    for ns in sorted(user_ns, key=lambda n: n.id):
        handle = ns.id.removeprefix(prefix).replace("_", ".")
        print(f"  {handle:<40} ({ns.id})")
    print("\nuse: uv run scripts/memory_versions.py HANDLE")
    print("  or: uv run scripts/memory_versions.py --all --summary")


if __name__ == "__main__":
    main()
