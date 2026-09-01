# ============================================================
#  infrastructure/document/image_cropper.py
#  Pillow Raster Image Cropping & Output Encoding
# ============================================================

import io
from PIL import Image
from core.entities.bounding_box import PixelRectangle


class ImageCropper:
    """
    Performs raster cropping on image bytes according to validated PixelRectangle geometries.
    """

    @staticmethod
    def crop_jpeg(image_bytes: bytes, rect: PixelRectangle, quality: int = 90) -> bytes:
        """
        Crops the specified pixel rectangle from image_bytes and returns encoded JPEG bytes.
        """
        with Image.open(io.BytesIO(image_bytes)) as img:
            # Normalize color modes (e.g. RGBA or palette) for JPEG encoding
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")

            cropped = img.crop(rect.as_tuple)
            buf = io.BytesIO()
            cropped.save(buf, format="JPEG", quality=quality)
            return buf.getvalue()

    @staticmethod
    def get_image_dimensions(image_bytes: bytes) -> tuple[int, int]:
        """
        Retrieves (width, height) in pixels from raw image bytes.
        """
        with Image.open(io.BytesIO(image_bytes)) as img:
            return img.size
