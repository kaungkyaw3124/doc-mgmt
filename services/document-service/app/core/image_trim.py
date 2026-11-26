from io import BytesIO

from PIL import Image, ImageChops


def trim_blank_margins(image_bytes):
    """Crop transparent (alpha < 10) and near-white (all channels >= 245) borders
    off a PNG/JPEG/WebP, keeping 2% of the shorter side (2-6 px) as padding.
    Anything else (SVG, corrupt, all blank, no border) comes back unchanged."""
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            fmt = img.format
            if fmt not in ("PNG", "JPEG", "WEBP"):
                return image_bytes
            rgba = img.convert("RGBA")
            # A pixel is content when it is visible and not near-white.
            visible = rgba.getchannel("A").point(lambda a: 255 if a >= 10 else 0)
            dark = ImageChops.darker(ImageChops.darker(*rgba.split()[:2]), rgba.getchannel("B")).point(lambda v: 255 if v < 245 else 0)
            bbox = ImageChops.multiply(visible, dark).getbbox()
            if not bbox:
                return image_bytes  # all blank: nothing sensible to crop to
            pad = min(6, max(2, round(min(img.size) * 0.02)))
            # A side whose blank margin is already <= 12 px (twice the largest padding,
            # room for JPEG re-encode jitter) stays as it is, so trimming is idempotent.
            margins = (bbox[0], bbox[1], img.width - bbox[2], img.height - bbox[3])
            if max(margins) <= 12:
                return image_bytes
            box = tuple(edge if m <= 12 else (b - pad if i < 2 else b + pad)
                        for i, (m, b, edge) in enumerate(zip(margins, bbox, (0, 0, img.width, img.height))))
            cropped = img.crop(box)
            out = BytesIO()
            cropped.save(out, fmt, **({"quality": 95} if fmt == "JPEG" else {}))
            return out.getvalue()
    except Exception:
        return image_bytes  # a broken image must never block an export
