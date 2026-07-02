"""Media tools for inspecting atproto record blobs."""

from typing import Annotated

from atproto import AtUri
from pydantic import Field
from pydantic_ai import BinaryContent, RunContext

from bot.core.media import fetch_blob_bytes, fetch_record, find_allowed_blobs
from bot.tools._helpers import PhiDeps

MAX_TEXT_BYTES = 64_000
MAX_IMAGE_BYTES = 5_000_000


def _decode_text(data: bytes, max_bytes: int = MAX_TEXT_BYTES) -> str:
    suffix = ""
    if len(data) > max_bytes:
        data = data[:max_bytes]
        suffix = "\n\n[truncated]"
    return data.decode("utf-8", errors="replace") + suffix


def register(agent):
    @agent.tool
    async def inspect_record_media(
        ctx: RunContext[PhiDeps],
        uri: Annotated[
            str,
            Field(
                description=(
                    "AT-URI of the record to inspect, e.g. "
                    "at://did:plc:.../collection/rkey. Use this when a "
                    "record may contain image or text blobs you need to see."
                )
            ),
        ],
        index: Annotated[
            int | None,
            Field(
                description=(
                    "Optional zero-based index into the allowed blobs found "
                    "on the record. Omit to inspect every allowed text/image "
                    "blob, up to size limits."
                )
            ),
        ] = None,
    ) -> list[str | BinaryContent] | str:
        """Look at text/image blobs stored on any atproto record.

        This is record-level media inspection, not a Bluesky-specific post
        reader. Use it after get_record/list_records/search tools surface an
        AT-URI whose value contains a blob, or when someone asks whether you
        can see an image stored on a non-Bluesky record. Allowed MIME types are
        text/plain, text/markdown, text/csv, JSON, PNG, JPEG, GIF, and WebP.
        Unsupported blobs are reported but not fetched.
        """
        try:
            parsed = AtUri.from_str(uri)
            record = await fetch_record(uri)
        except Exception as e:
            return f"could not fetch record {uri!r}: {type(e).__name__}: {e}"

        blobs = find_allowed_blobs(record.get("value") or {})
        if not blobs:
            return (
                f"record {uri} has no allowed text/image blobs. "
                "Allowed MIME types: text, JSON, PNG, JPEG, GIF, WebP."
            )

        if index is not None:
            if index < 0 or index >= len(blobs):
                return f"blob index {index} out of range; record has {len(blobs)} allowed blobs"
            selected = [(index, blobs[index])]
        else:
            selected = list(enumerate(blobs))

        parts: list[str | BinaryContent] = [
            f"record {record.get('uri', uri)} contains {len(blobs)} allowed blob(s)."
        ]
        for i, blob in selected:
            size_part = f", {blob.size} bytes" if blob.size is not None else ""
            label = f"blob {i} at {blob.path}: {blob.mime_type}{size_part}"
            if blob.size is not None and blob.is_image and blob.size > MAX_IMAGE_BYTES:
                parts.append(
                    f"{label} - skipped: image exceeds {MAX_IMAGE_BYTES} bytes"
                )
                continue
            try:
                data = await fetch_blob_bytes(parsed.host, blob.cid)
            except Exception as e:
                parts.append(f"{label} - fetch failed: {type(e).__name__}: {e}")
                continue

            if blob.is_text:
                parts.append(f"{label}\n{_decode_text(data)}")
            elif blob.is_image:
                parts.append(label)
                parts.append(BinaryContent(data=data, media_type=blob.mime_type))

        return parts
