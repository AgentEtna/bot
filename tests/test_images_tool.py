"""generate_image: compression respects grain's 1MB blob cap."""

import io


def _png(w, h):
    from PIL import Image

    img = Image.new("RGB", (w, h), (30, 60, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_compress_stays_under_blob_cap():
    from bot.tools.images import _MAX_BLOB_BYTES, _compress_to_limit

    data, mime, w, h = _compress_to_limit(_png(1536, 1024))
    assert len(data) <= _MAX_BLOB_BYTES
    assert mime == "image/jpeg"
    assert (w, h) == (1536, 1024)


def test_compress_converts_mode():
    from PIL import Image

    from bot.tools.images import _compress_to_limit

    img = Image.new("RGBA", (64, 64), (255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data, mime, w, h = _compress_to_limit(buf.getvalue())
    assert mime == "image/jpeg" and (w, h) == (64, 64)
