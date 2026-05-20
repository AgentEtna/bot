"""Test cosmik record types — validation and serialization."""

import pytest
from pydantic import ValidationError

from bot.types import (
    CosmikCollection,
    CosmikCollectionLink,
    CosmikConnection,
    CosmikNoteCard,
    CosmikUrlCard,
    NoteContent,
    StrongRef,
    UrlContent,
)

# --- CosmikConnection ---


def test_connection_valid():
    conn = CosmikConnection(
        source="https://example.com",
        target="at://did:plc:abc/app.bsky.feed.post/123",
        connection_type="RELATED",
        note="test",
    )
    assert conn.source == "https://example.com"
    assert conn.connection_type == "RELATED"


def test_connection_to_record_full():
    conn = CosmikConnection(
        source="https://a.com",
        target="https://b.com",
        connection_type="SUPPORTS",
        note="because reasons",
    )
    record = conn.to_record()
    assert record["source"] == "https://a.com"
    assert record["target"] == "https://b.com"
    assert record["connectionType"] == "SUPPORTS"
    assert record["note"] == "because reasons"
    assert "createdAt" in record
    assert "updatedAt" in record


def test_connection_to_record_minimal():
    conn = CosmikConnection(source="https://a.com", target="https://b.com")
    record = conn.to_record()
    assert record["source"] == "https://a.com"
    assert record["target"] == "https://b.com"
    assert "connectionType" not in record
    assert "note" not in record
    assert "createdAt" in record
    assert "updatedAt" in record


def test_connection_rejects_bare_string():
    with pytest.raises(ValidationError):
        CosmikConnection(source="not-a-url", target="https://b.com")


def test_connection_rejects_invalid_type():
    with pytest.raises(ValidationError):
        CosmikConnection(
            source="https://a.com",
            target="https://b.com",
            connection_type="invented",
        )


# --- CosmikNoteCard ---


def test_note_card_valid():
    card = CosmikNoteCard(content=NoteContent(text="hello world"))
    assert card.type == "NOTE"
    assert card.content.text == "hello world"


def test_note_card_to_record():
    card = CosmikNoteCard(content=NoteContent(text="a thought"))
    record = card.to_record()
    assert record["type"] == "NOTE"
    assert record["content"]["$type"] == "network.cosmik.card#noteContent"
    assert record["content"]["text"] == "a thought"
    assert "createdAt" in record
    assert "parentCard" not in record


def test_note_card_rejects_empty():
    # pydantic allows empty string for str fields — max_length is the guard
    card = CosmikNoteCard(content=NoteContent(text=""))
    assert card.content.text == ""


def test_note_card_rejects_too_long():
    with pytest.raises(ValidationError):
        CosmikNoteCard(content=NoteContent(text="x" * 10001))


# --- CosmikUrlCard ---


def test_url_card_valid():
    card = CosmikUrlCard(
        content=UrlContent(
            url="https://example.com", title="Example", description="A site"
        )
    )
    assert card.type == "URL"
    assert card.content.url == "https://example.com"


def test_url_card_to_record_full():
    card = CosmikUrlCard(
        content=UrlContent(url="https://example.com", title="Ex", description="desc")
    )
    record = card.to_record()
    assert record["type"] == "URL"
    assert record["content"]["$type"] == "network.cosmik.card#urlContent"
    assert record["content"]["url"] == "https://example.com"
    assert record["content"]["metadata"]["$type"] == "network.cosmik.card#urlMetadata"
    assert record["content"]["metadata"]["title"] == "Ex"
    assert record["content"]["metadata"]["description"] == "desc"
    assert "createdAt" in record


def test_url_card_to_record_minimal():
    card = CosmikUrlCard(content=UrlContent(url="https://example.com"))
    record = card.to_record()
    assert record["type"] == "URL"
    assert record["content"]["$type"] == "network.cosmik.card#urlContent"
    assert record["content"]["url"] == "https://example.com"
    assert "metadata" not in record["content"]
    assert "createdAt" in record


def test_url_card_rejects_bare_string():
    with pytest.raises(ValidationError):
        CosmikUrlCard(content=UrlContent(url="not-a-url"))


def test_url_card_accepts_at_uri():
    card = CosmikUrlCard(content=UrlContent(url="at://did:plc:abc/collection/rkey"))
    assert card.content.url == "at://did:plc:abc/collection/rkey"


# --- CosmikNoteCard with parentCard ---


def test_note_card_with_parent():
    parent = StrongRef(uri="at://did:plc:abc/network.cosmik.card/xyz", cid="bafyabc")
    card = CosmikNoteCard(content=NoteContent(text="child note"), parent_card=parent)
    record = card.to_record()
    assert record["parentCard"] == {"uri": parent.uri, "cid": parent.cid}


# --- CosmikCollection ---


def test_collection_to_record():
    coll = CosmikCollection(name="epistemology", description="memory and knowledge")
    record = coll.to_record()
    assert record == {
        "name": "epistemology",
        "accessType": "OPEN",
        "description": "memory and knowledge",
    }


def test_collection_minimal():
    coll = CosmikCollection(name="misc")
    record = coll.to_record()
    assert record == {"name": "misc", "accessType": "OPEN"}


def test_collection_rejects_long_name():
    with pytest.raises(ValidationError):
        CosmikCollection(name="x" * 101)


# --- CosmikCollectionLink ---


def test_collection_link_to_record():
    link = CosmikCollectionLink(
        collection=StrongRef(
            uri="at://did:plc:abc/network.cosmik.collection/c1", cid="bafycol"
        ),
        card=StrongRef(uri="at://did:plc:abc/network.cosmik.card/k1", cid="bafycard"),
        added_by="did:plc:abc",
        added_at="2026-04-01T00:00:00Z",
    )
    record = link.to_record()
    assert (
        record["collection"]["uri"] == "at://did:plc:abc/network.cosmik.collection/c1"
    )
    assert record["card"]["cid"] == "bafycard"
    assert record["addedBy"] == "did:plc:abc"
    assert record["addedAt"] == "2026-04-01T00:00:00Z"
