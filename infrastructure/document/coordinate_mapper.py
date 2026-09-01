# ============================================================
#  infrastructure/document/coordinate_mapper.py
#  Deterministic Coordinate Mapping, Clamping & Padding
# ============================================================

import math
from typing import Optional
from core.entities.bounding_box import BoundingBox, PixelRectangle, CropPolicy


class CoordinateMapper:
    """
    Translates canonical normalized bounding boxes (0..1000) into clamped,
    padded pixel rectangles relative to concrete raster image dimensions.
    """

    @staticmethod
    def map_to_pixels(
        box: BoundingBox,
        image_width: int,
        image_height: int,
        policy: Optional[CropPolicy] = None,
    ) -> Optional[PixelRectangle]:
        """
        Maps a normalized BoundingBox to a clamped PixelRectangle.

        Formula:
          left   = floor(xmin * W / 1000)
          top    = floor(ymin * H / 1000)
          right  = floor(xmax * W / 1000)
          bottom = floor(ymax * H / 1000)

        Returns None if the resulting geometry is zero-area, inverted, or below min_size.
        """
        if image_width <= 0 or image_height <= 0:
            return None

        if box.is_zero_area:
            return None

        policy = policy or CropPolicy()

        raw_left = int(math.floor(box.xmin * image_width / 1000.0))
        raw_top = int(math.floor(box.ymin * image_height / 1000.0))
        raw_right = int(math.floor(box.xmax * image_width / 1000.0))
        raw_bottom = int(math.floor(box.ymax * image_height / 1000.0))

        # Apply configurable padding
        pad_x = 0
        pad_y = 0
        if policy.padding_percent > 0:
            pad_x = int(math.floor(image_width * policy.padding_percent))
            pad_y = int(math.floor(image_height * policy.padding_percent))

        # Clamp padded boundaries strictly to [0, image_width] and [0, image_height]
        left = max(0, raw_left - pad_x)
        top = max(0, raw_top - pad_y)
        right = min(image_width, raw_right + pad_x)
        bottom = min(image_height, raw_bottom + pad_y)

        # Validate minimum bounds
        if right <= left or bottom <= top:
            return None

        width_px = right - left
        height_px = bottom - top

        if width_px < policy.min_width or height_px < policy.min_height:
            return None

        try:
            return PixelRectangle(left=left, top=top, right=right, bottom=bottom)
        except ValueError:
            return None
