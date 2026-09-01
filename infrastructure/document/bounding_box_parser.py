# ============================================================
#  infrastructure/document/bounding_box_parser.py
#  Re-export of core BoundingBoxParser for backwards compatibility
# ============================================================

from core.entities.bounding_box_parser import (
    ParsedBoundingBoxMatch,
    BoundingBoxParser,
)

__all__ = ["ParsedBoundingBoxMatch", "BoundingBoxParser"]
