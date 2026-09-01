# ============================================================
#  core/entities/bounding_box.py
#  Canonical Provider-Independent Bounding Box Entity & Policy
# ============================================================

from dataclasses import dataclass
from typing import Tuple


class InvalidBoundingBoxError(ValueError):
    """Raised when bounding box coordinates violate domain invariants."""
    pass


@dataclass(frozen=True)
class BoundingBox:
    """
    Canonical provider-independent normalized bounding box representation.
    Coordinates are defined on a normalized [0, 1000] integer grid.

    Invariants:
      0 <= ymin <= ymax <= 1000
      0 <= xmin <= xmax <= 1000

    Origin: Top-Left (0, 0)
    Axis orientation:
      X increases rightward (xmin -> xmax)
      Y increases downward (ymin -> ymax)
    """
    ymin: int
    xmin: int
    ymax: int
    xmax: int

    def __post_init__(self):
        for name, val in [("ymin", self.ymin), ("xmin", self.xmin), ("ymax", self.ymax), ("xmax", self.xmax)]:
            if not isinstance(val, int) or isinstance(val, bool):
                raise InvalidBoundingBoxError(
                    f"Coordinate '{name}' must be an integer, got {type(val).__name__} ({val})"
                )
            if val < 0 or val > 1000:
                raise InvalidBoundingBoxError(
                    f"Coordinate '{name}' ({val}) is out of normalized bounds [0, 1000]"
                )

        if self.ymin > self.ymax:
            raise InvalidBoundingBoxError(f"Inverted Y bounds: ymin ({self.ymin}) > ymax ({self.ymax})")
        if self.xmin > self.xmax:
            raise InvalidBoundingBoxError(f"Inverted X bounds: xmin ({self.xmin}) > xmax ({self.xmax})")

    @property
    def width(self) -> int:
        """Normalized width on the 0..1000 scale."""
        return self.xmax - self.xmin

    @property
    def height(self) -> int:
        """Normalized height on the 0..1000 scale."""
        return self.ymax - self.ymin

    @property
    def area(self) -> int:
        """Normalized area on the 0..1000^2 scale."""
        return self.width * self.height

    @property
    def is_zero_area(self) -> bool:
        """Returns True if the bounding box has zero width or zero height."""
        return self.width == 0 or self.height == 0

    @property
    def as_tuple(self) -> Tuple[int, int, int, int]:
        """Returns (ymin, xmin, ymax, xmax)."""
        return (self.ymin, self.xmin, self.ymax, self.xmax)


@dataclass(frozen=True)
class PixelRectangle:
    """
    Pixel-space crop coordinates in the raster image domain.
    Coordinates represent [left, top, right, bottom] with origin at top-left.
    (left, top) is inclusive, (right, bottom) is exclusive.
    """
    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self):
        for name, val in [("left", self.left), ("top", self.top), ("right", self.right), ("bottom", self.bottom)]:
            if not isinstance(val, int) or isinstance(val, bool):
                raise ValueError(f"Pixel coordinate '{name}' must be an integer, got {type(val).__name__}")
            if val < 0:
                raise ValueError(f"Pixel coordinate '{name}' cannot be negative ({val})")

        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError(
                f"Invalid pixel rectangle geometry: left={self.left}, top={self.top}, "
                f"right={self.right}, bottom={self.bottom}"
            )

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def as_tuple(self) -> Tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)


@dataclass(frozen=True)
class CropPolicy:
    """
    Configurable policy governing safety padding and minimum dimensions for image cropping.
    """
    padding_percent: float = 0.015  # 1.5% of dimension by default
    min_width: int = 16
    min_height: int = 16
    jpeg_quality: int = 90
