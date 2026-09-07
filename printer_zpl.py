"""Pure helpers for converting Pillow images to Zebra ZPL raster data."""

from PIL import Image


def image_to_zpl_bitmap(image: Image.Image, threshold: int = 180) -> bytes:
    """Pack an image using the bit polarity expected by ZPL ``^GFA``.

    Pillow mode ``1`` uses zero bits for black and one bits for white. ZPL
    graphics use the opposite convention: a one bit prints a black dot. The
    intentionally reversed mode-1 values below produce ZPL-ready bytes while
    leaving Pillow's unused end-of-row padding bits at zero (unprinted).
    """
    grayscale = image.convert("L")
    return grayscale.point(
        lambda pixel: 255 if pixel < threshold else 0,
        mode="1",
    ).tobytes()
