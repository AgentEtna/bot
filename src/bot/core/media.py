"""Generic atproto media/blob reading primitives.

This module deliberately stays below Bluesky post semantics. It knows about
atproto records, blob refs, MIME allowlists, and sync.getBlob; callers decide
whether the record came from a post, a cosmik card, a game record, or anything
else on a PDS.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from atproto import AtUri
from atproto_client.models.utils import get_model_as_dict

from bot.core.atproto_client import bot_client

PDS_ENTRYWAY = "https://bsky.social"

ALLOWED_TEXT_MIME_TYPES = {
    "application/json",
    "application/ld+json",
    "text/csv",
    "text/markdown",
    "text/plain",
}
ALLOWED_IMAGE_MIME_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
ALLOWED_MEDIA_MIME_TYPES = ALLOWED_TEXT_MIME_TYPES | ALLOWED_IMAGE_MIME_TYPES


@dataclass(frozen=True)
class AtprotoBlobRef:
    """A blob reference found inside an atproto record."""

    cid: str
    mime_type: str
    size: int | None
    path: str

    @property
    def is_text(self) -> bool:
        return self.mime_type in ALLOWED_TEXT_MIME_TYPES

    @property
    def is_image(self) -> bool:
        return self.mime_type in ALLOWED_IMAGE_MIME_TYPES


def _plain(value: Any) -> Any:
    """Convert SDK model/DotDict values to plain Python containers."""
    try:
        return get_model_as_dict(value)
    except Exception:
        return value


def _mapping_items(value: Any) -> Iterator[tuple[str, Any]]:
    value = _plain(value)
    if isinstance(value, Mapping):
        yield from value.items()
        return
    if hasattr(value, "__dict__"):
        yield from vars(value).items()


def _list_items(value: Any) -> Iterator[Any]:
    value = _plain(value)
    if isinstance(value, list | tuple):
        yield from value


def _blob_cid(value: Mapping[str, Any]) -> str:
    ref = value.get("ref") or {}
    if isinstance(ref, Mapping):
        return str(ref.get("$link") or ref.get("cid") or "")
    return str(value.get("cid") or "")


def find_allowed_blobs(record: Any) -> list[AtprotoBlobRef]:
    """Return allowed text/image blob refs found anywhere inside *record*.

    The allowlist is intentionally narrow: text and images only. Unknown,
    missing, video/audio, and application/octet-stream blobs are ignored here
    because the model cannot safely infer how to inspect them generically.
    """
    found: list[AtprotoBlobRef] = []

    def walk(value: Any, path: str) -> None:
        plain = _plain(value)
        if isinstance(plain, Mapping):
            cid = _blob_cid(plain)
            mime_type = str(plain.get("mimeType") or plain.get("mime_type") or "")
            if cid and mime_type in ALLOWED_MEDIA_MIME_TYPES:
                size = plain.get("size")
                found.append(
                    AtprotoBlobRef(
                        cid=cid,
                        mime_type=mime_type,
                        size=size if isinstance(size, int) else None,
                        path=path or "$",
                    )
                )
                return
            for key, child in plain.items():
                walk(child, f"{path}.{key}" if path else str(key))
            return

        for i, child in enumerate(_list_items(plain)):
            walk(child, f"{path}[{i}]" if path else f"[{i}]")

    walk(record, "")
    return found


async def fetch_record(uri: str) -> dict[str, Any]:
    """Fetch any atproto record by AT-URI as a plain dict."""
    parsed = AtUri.from_str(uri)
    if not parsed.collection or not parsed.rkey:
        msg = f"AT-URI must include collection and rkey: {uri}"
        raise ValueError(msg)

    await bot_client.authenticate()
    result = bot_client.client.com.atproto.repo.get_record(
        {"repo": parsed.host, "collection": parsed.collection, "rkey": parsed.rkey}
    )
    return {
        "uri": result.uri,
        "cid": result.cid,
        "value": _plain(result.value),
    }


async def fetch_blob_bytes(
    did: str,
    blob_cid: str,
    *,
    timeout: float = 30,
    pds_entryway: str = PDS_ENTRYWAY,
) -> bytes:
    """Fetch raw blob bytes through com.atproto.sync.getBlob."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(
            f"{pds_entryway}/xrpc/com.atproto.sync.getBlob",
            params={"did": did, "cid": blob_cid},
        )
        resp.raise_for_status()
        return resp.content

