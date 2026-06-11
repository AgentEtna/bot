"""Validated types for atproto records phi creates."""

import time
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

_TID_CHARSET = "234567abcdefghijklmnopqrstuvwxyz"


class Bio(BaseModel):
    """Structured output for phi's bsky profile bio.

    Bsky's `app.bsky.actor.profile.description` field is capped at 256
    graphemes — pydantic's max_length here treats it as 256 chars, which
    is the conservative reading. Phi writes a fresh bio at every startup
    via `PhiAgent.process_bio`.
    """

    text: str = Field(
        ...,
        max_length=256,
        description=(
            "The new bio text. Communicate what you are, your capabilities, "
            "and who your operator is. Plain text. Include a 🟢 somewhere "
            "if you want the operator's pause/resume system to be able to "
            "swap it to 🔴 on shutdown."
        ),
    )


def generate_tid() -> str:
    """Generate an AT Protocol TID (timestamp identifier).

    13-char base32-sortstring encoding microsecond timestamp + clock_id.
    """
    us = int(time.time() * 1_000_000)
    n = (us << 10) | 0  # clock_id = 0
    chars = []
    for _ in range(13):
        chars.append(_TID_CHARSET[n & 0x1F])
        n >>= 5
    return "".join(reversed(chars))


# --- validators ---


def _validate_entity_ref(v: str) -> str:
    """Must be a URL or at:// URI."""
    if v.startswith(("at://", "https://", "http://")):
        return v
    raise ValueError(f"must be a URL or at:// URI, got: {v!r}")


EntityRef = Annotated[str, AfterValidator(_validate_entity_ref)]


# --- shared ---


class StrongRef(BaseModel):
    """AT Protocol strong reference — uri + cid pair."""

    uri: EntityRef
    cid: str


# --- records ---


class GreenGaleDocument(BaseModel):
    """app.greengale.document record — a long-form markdown blog post.

    Published to phi's PDS, rendered at greengale.app/{handle}/{rkey},
    and indexed by pub-search for discoverability.
    """

    title: str = Field(max_length=1000)
    content: str = Field(max_length=100000)
    tags: list[str] = Field(default_factory=list)
    visibility: Literal["public", "url", "author"] = "public"

    def to_record(self, handle: str, rkey: str) -> dict:
        return {
            "$type": "app.greengale.document",
            "content": self.content,
            "title": self.title,
            "url": f"https://greengale.app/{handle}",
            "path": f"/{rkey}",
            "publishedAt": datetime.now(UTC).isoformat(),
            "visibility": self.visibility,
            "tags": self.tags,
        }
