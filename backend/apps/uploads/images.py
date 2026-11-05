"""Image sanitising: decode with Pillow, re-encode to WebP.

Re-encoding drops EXIF/GPS metadata, ICC tricks and polyglot payloads appended to the file,
and rejects anything Pillow can't actually decode as an image.
"""

import io

from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard (~40 MP)
MAX_DIMENSION = 2048
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


class InvalidImage(Exception):
    pass


def sanitise_to_webp(data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise InvalidImage("unsupported format")
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)  # keep the photo upright once EXIF is gone
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
            img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
            out = io.BytesIO()
            img.save(out, "WEBP", quality=85, method=4)  # no exif= argument -> metadata dropped
            return out.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise InvalidImage(str(exc)) from None
