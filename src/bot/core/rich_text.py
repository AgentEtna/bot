"""Rich text handling for Bluesky posts"""

import re
from typing import Any

from atproto import Client

MENTION_REGEX = rb"(?:^|[$|\W])(@([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)"
# a comma is legal inside a path/fragment (lexidraw.app/#atproto=<did>,<rkey>)
# but a trailing comma is punctuation: the last character class excludes it.
URL_REGEX = rb"(?:^|[$|\W])(https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=,]*[-a-zA-Z0-9@%_\+~#//=])?)"
# a bare domain must start a token: not the tail of a path (docs/memory.md
# is a file, not the Moldovan domain memory.md), not a handle (@phi.zzstoatzz.io
# is a mention), not the inside of a longer hostname.
BARE_URL_REGEX = rb"(?<![\w/.@-])((?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(?:/[-a-zA-Z0-9@:%_\+.~#?&//=,]*[-a-zA-Z0-9@%_\+~#//=])?)"


def parse_mentions(
    text: str, client: Client, allowed_handles: set[str] | None = None
) -> list[dict[str, Any]]:
    """Parse @mentions and create facets with proper byte positions.

    If *allowed_handles* is provided, only handles in the set get a mention
    facet (which sends a notification).  All other @handles are left as plain
    text — visible but silent.
    """
    facets = []
    text_bytes = text.encode("UTF-8")

    for match in re.finditer(MENTION_REGEX, text_bytes):
        handle = match.group(1)[1:].decode("UTF-8")  # Remove @ prefix

        # consent gate: skip handles not in the allowlist
        if allowed_handles is not None and handle not in allowed_handles:
            continue

        mention_start = match.start(1)
        mention_end = match.end(1)

        try:
            # Resolve handle to DID
            response = client.com.atproto.identity.resolve_handle(
                params={"handle": handle}
            )
            did = response.did

            facets.append(
                {
                    "index": {
                        "byteStart": mention_start,
                        "byteEnd": mention_end,
                    },
                    "features": [
                        {"$type": "app.bsky.richtext.facet#mention", "did": did}
                    ],
                }
            )
        except Exception:
            # Skip if handle can't be resolved
            continue

    return facets


def parse_urls(text: str) -> list[dict[str, Any]]:
    """Parse URLs and create link facets (full https?:// URLs and bare domain URLs)"""
    facets = []
    text_bytes = text.encode("UTF-8")
    covered: set[tuple[int, int]] = set()

    # full URLs first (https://...)
    for match in re.finditer(URL_REGEX, text_bytes):
        url = match.group(1).decode("UTF-8")
        url_start = match.start(1)
        url_end = match.end(1)
        covered.add((url_start, url_end))

        facets.append(
            {
                "index": {
                    "byteStart": url_start,
                    "byteEnd": url_end,
                },
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
            }
        )

    # bare domain URLs (e.g. cnbc.com/path) — skip if overlapping a full URL
    for match in re.finditer(BARE_URL_REGEX, text_bytes):
        bare_start = match.start(1)
        bare_end = match.end(1)
        if any(not (bare_end <= cs or bare_start >= ce) for cs, ce in covered):
            continue
        bare = match.group(1).decode("UTF-8")
        facets.append(
            {
                "index": {
                    "byteStart": bare_start,
                    "byteEnd": bare_end,
                },
                "features": [
                    {
                        "$type": "app.bsky.richtext.facet#link",
                        "uri": f"https://{bare}",
                    }
                ],
            }
        )

    return facets


def create_facets(
    text: str, client: Client, allowed_handles: set[str] | None = None
) -> list[dict[str, Any]]:
    """Create all facets for a post (mentions and URLs).

    *allowed_handles* gates which @mentions become notification-sending facets.
    See :func:`parse_mentions` for details.
    """
    facets = []
    facets.extend(parse_mentions(text, client, allowed_handles))
    facets.extend(parse_urls(text))
    return facets
