"""generate_image — phi makes a picture and lands it on her own PDS as a blob.

The bytes never enter model context: generation, compression, and
com.atproto.repo.uploadBlob all happen inside the tool. Phi receives only
the blob reference JSON, which she can embed in any record via pdsx —
grain photos (social.grain.photo), her bsky avatar/banner
(app.bsky.actor.profile/self), embeds, whatever the lexicon accepts.
The grain-photos skill carries the record shapes.
"""

import io
import json
import logging
from typing import Annotated, Literal

from pydantic import Field

from bot.config import settings
from bot.core.atproto_client import bot_client

logger = logging.getLogger("bot.tools.images")

# grain's social.grain.photo lexicon caps the blob at 1MB; staying under it
# keeps one generated image usable everywhere (grain, avatar, banner)
_MAX_BLOB_BYTES = 950_000

_SIZES: dict[str, tuple[int, int]] = {
    "square": (1024, 1024),
    "portrait": (1024, 1536),
    "landscape": (1536, 1024),
}


def _compress_to_limit(png_bytes: bytes) -> tuple[bytes, str, int, int]:
    """Re-encode to JPEG under the blob cap. Returns (bytes, mime, w, h)."""
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    for quality in (92, 85, 78, 70, 60):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= _MAX_BLOB_BYTES:
            return data, "image/jpeg", w, h
    # last resort: halve dimensions once and take the lowest quality
    img = img.resize((w // 2, h // 2))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60, optimize=True)
    return buf.getvalue(), "image/jpeg", w // 2, h // 2


def register(agent):
    @agent.tool_plain
    async def generate_image(
        prompt: Annotated[
            str,
            Field(
                description=(
                    "what the image should be. you are the author — write it "
                    "with the same specificity you'd want in a post, not a "
                    "keyword pile."
                )
            ),
        ],
        aspect: Annotated[
            Literal["square", "portrait", "landscape"],
            Field(description="canvas shape"),
        ] = "square",
    ) -> str:
        """Make an image and upload it to your own PDS as a blob.

        Generation and upload happen inside the tool; you get back the blob
        reference JSON plus width/height. Embed that blob object verbatim in
        whatever record you're composing via pdsx:
        - social.grain.photo (grain photos — load the grain-photos skill)
        - app.bsky.actor.profile/self avatar or banner
        The blob is inert until a record references it; generating an image
        posts nothing anywhere.
        """
        if not settings.openai_api_key:
            return "image generation is not configured (no api key)"
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key)
            w, h = _SIZES[aspect]
            result = await client.images.generate(
                model="gpt-image-1",
                prompt=prompt,
                size=f"{w}x{h}",
                quality="high",
            )
            import base64

            raw = base64.b64decode(result.data[0].b64_json)
        except Exception as e:
            logger.warning(f"image generation failed: {e}")
            return f"image generation failed: {type(e).__name__}: {str(e)[:200]}"

        try:
            data, mime, width, height = _compress_to_limit(raw)
            await bot_client.authenticate()
            resp = bot_client.client.upload_blob(data)
            blob = resp.blob.model_dump(mode="json", by_alias=True)
        except Exception as e:
            logger.exception(f"blob upload failed: {e}")
            return f"generated, but blob upload failed: {type(e).__name__}: {str(e)[:200]}"

        logger.info(
            f"generated image ({width}x{height}, {len(data)} bytes) "
            f"blob {blob.get('ref')}"
        )
        return json.dumps(
            {
                "blob": blob,
                "aspectRatio": {"width": width, "height": height},
                "bytes": len(data),
                "note": (
                    "embed `blob` verbatim as the blob field of your record "
                    "(e.g. social.grain.photo's `photo`). aspectRatio is "
                    "ready to copy into grain records."
                ),
            }
        )
