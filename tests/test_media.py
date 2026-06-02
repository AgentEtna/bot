from unittest.mock import AsyncMock, patch

from pydantic_ai import BinaryContent

from bot.core.media import find_allowed_blobs
from bot.tools import media as media_tool


class FakeAgent:
    def __init__(self):
        self.tool_func = None

    def tool(self, fn):
        self.tool_func = fn
        return fn


def _registered_tool():
    fake_agent = FakeAgent()
    media_tool.register(fake_agent)
    assert fake_agent.tool_func is not None
    return fake_agent.tool_func


def test_find_allowed_blobs_walks_nested_record():
    record = {
        "$type": "tech.waow.doodl",
        "title": "frog",
        "image": {
            "$type": "blob",
            "ref": {"$link": "bafyimg"},
            "mimeType": "image/png",
            "size": 1234,
        },
        "attachments": [
            {
                "$type": "blob",
                "ref": {"$link": "bafytext"},
                "mimeType": "text/plain",
                "size": 12,
            }
        ],
        "video": {
            "$type": "blob",
            "ref": {"$link": "bafyvideo"},
            "mimeType": "video/mp4",
            "size": 999,
        },
    }

    blobs = find_allowed_blobs(record)

    assert [b.cid for b in blobs] == ["bafyimg", "bafytext"]
    assert [b.path for b in blobs] == ["image", "attachments[0]"]
    assert blobs[0].is_image
    assert blobs[1].is_text


async def test_inspect_record_media_returns_image_binary_content():
    tool_func = _registered_tool()

    record = {
        "uri": "at://did:plc:abc/tech.waow.doodl/one",
        "cid": "bafyrec",
        "value": {
            "image": {
                "$type": "blob",
                "ref": {"$link": "bafyimg"},
                "mimeType": "image/png",
                "size": 4,
            }
        },
    }

    with (
        patch.object(media_tool, "fetch_record", new=AsyncMock(return_value=record)),
        patch.object(
            media_tool, "fetch_blob_bytes", new=AsyncMock(return_value=b"\x89PNG")
        ),
    ):
        result = await tool_func(None, record["uri"])

    assert isinstance(result, list)
    assert "contains 1 allowed blob" in result[0]
    assert "blob 0 at image: image/png" in result[1]
    assert isinstance(result[2], BinaryContent)
    assert result[2].data == b"\x89PNG"
    assert result[2].media_type == "image/png"


async def test_inspect_record_media_decodes_text_blob():
    tool_func = _registered_tool()

    record = {
        "uri": "at://did:plc:abc/app.example.note/one",
        "cid": "bafyrec",
        "value": {
            "body": {
                "$type": "blob",
                "ref": {"$link": "bafytext"},
                "mimeType": "text/plain",
                "size": 11,
            }
        },
    }

    with (
        patch.object(media_tool, "fetch_record", new=AsyncMock(return_value=record)),
        patch.object(
            media_tool, "fetch_blob_bytes", new=AsyncMock(return_value=b"hello world")
        ),
    ):
        result = await tool_func(None, record["uri"])

    assert isinstance(result, list)
    assert "hello world" in result[1]
