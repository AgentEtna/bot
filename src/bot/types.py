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

ConnectionType = Literal[
    "RELATED",
    "SUPPORTS",
    "OPPOSES",
    "ADDRESSES",
    "HELPFUL",
    "EXPLAINER",
    "LEADS_TO",
    "SUPPLEMENTS",
]


# --- content models ---


class NoteContent(BaseModel):
    """Content for a NOTE-type cosmik card."""

    text: str = Field(max_length=10000)


class UrlMetadata(BaseModel):
    """Metadata about a URL (nested under content.metadata per semble lexicon)."""

    title: str | None = None
    description: str | None = None


class UrlContent(BaseModel):
    """Content for a URL-type cosmik card."""

    url: EntityRef
    title: str | None = None
    description: str | None = None


# --- shared ---


class StrongRef(BaseModel):
    """AT Protocol strong reference — uri + cid pair."""

    uri: EntityRef
    cid: str


# --- records ---


class CosmikConnection(BaseModel):
    """network.cosmik.connection record.

    A directed edge between two entities (URLs or cards) with optional
    semantic type and note. Schema lives at:
    at://cosmik.network/com.atproto.lexicon.schema/network.cosmik.connection
    """

    model_config = {"populate_by_name": True}

    source: EntityRef = Field(description="source entity — URL or at:// URI")
    target: EntityRef = Field(description="target entity — URL or at:// URI")
    connection_type: ConnectionType | None = Field(
        default=None,
        alias="connectionType",
        description="semantic relationship type",
    )
    note: str | None = Field(
        default=None,
        max_length=1000,
        description="optional context about the connection",
    )

    def to_record(self) -> dict:
        """Serialize to the shape expected by com.atproto.repo.createRecord."""
        now = datetime.now(UTC).isoformat()
        record: dict = {
            "source": self.source,
            "target": self.target,
            "createdAt": now,
            "updatedAt": now,
        }
        if self.connection_type:
            record["connectionType"] = self.connection_type
        if self.note:
            record["note"] = self.note
        return record


class CosmikNoteCard(BaseModel):
    """network.cosmik.card record — NOTE type.

    Semble requires a parentCard (strongRef to an existing URL card) for NOTE
    records. Standalone notes are dropped by the firehose handler.
    """

    type: Literal["NOTE"] = "NOTE"
    content: NoteContent
    parent_card: StrongRef | None = None

    def to_record(self) -> dict:
        record: dict = {
            "type": self.type,
            "content": {
                "$type": "network.cosmik.card#noteContent",
                "text": self.content.text,
            },
            "createdAt": datetime.now(UTC).isoformat(),
        }
        if self.parent_card:
            record["parentCard"] = {
                "uri": self.parent_card.uri,
                "cid": self.parent_card.cid,
            }
        return record


class CosmikUrlCard(BaseModel):
    """network.cosmik.card record — URL type.

    A bookmarked URL stored on-protocol as a cosmik card. Indexed by semble.
    Metadata (title, description) goes under content.metadata per semble lexicon.
    """

    type: Literal["URL"] = "URL"
    content: UrlContent

    def to_record(self) -> dict:
        record: dict = {
            "type": self.type,
            "content": {
                "$type": "network.cosmik.card#urlContent",
                "url": self.content.url,
            },
            "createdAt": datetime.now(UTC).isoformat(),
        }
        metadata: dict = {}
        if self.content.title:
            metadata["title"] = self.content.title
        if self.content.description:
            metadata["description"] = self.content.description
        if metadata:
            record["content"]["metadata"] = {
                "$type": "network.cosmik.card#urlMetadata",
                **metadata,
            }
        return record


class CosmikCollection(BaseModel):
    """network.cosmik.collection record — a named grouping of cards.

    Schema: at://cosmik.network/com.atproto.lexicon.schema/network.cosmik.collection
    """

    name: str = Field(max_length=100)
    access_type: Literal["OPEN", "CLOSED"] = "OPEN"
    description: str | None = Field(default=None, max_length=500)

    def to_record(self) -> dict:
        record: dict = {"name": self.name, "accessType": self.access_type}
        if self.description:
            record["description"] = self.description
        return record


class CosmikCollectionLink(BaseModel):
    """network.cosmik.collectionLink record — joins a card to a collection.

    Requires strongRefs (uri + cid) for both collection and card.
    """

    collection: StrongRef
    card: StrongRef
    added_by: str
    added_at: str

    def to_record(self) -> dict:
        return {
            "collection": {"uri": self.collection.uri, "cid": self.collection.cid},
            "card": {"uri": self.card.uri, "cid": self.card.cid},
            "addedBy": self.added_by,
            "addedAt": self.added_at,
        }


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
