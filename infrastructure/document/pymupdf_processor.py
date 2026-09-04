# ============================================================
#  infrastructure/document/pymupdf_processor.py
#  High-Precision PDF Page Rendering & Image Extraction
# ============================================================

import re
from typing import List, Tuple, Optional
try:
    import pymupdf as fitz
except ImportError:
    import fitz

from application.ports.document_processor import IDocumentProcessor, ExtractedCrop
from core.entities.bounding_box import BoundingBox, CropPolicy
from infrastructure.document.bounding_box_parser import BoundingBoxParser
from infrastructure.document.coordinate_mapper import CoordinateMapper
from infrastructure.document.image_cropper import ImageCropper


class PyMuPDFDocumentProcessor(IDocumentProcessor):
    """
    High-precision document processor implementing IDocumentProcessor.
    Coordinates PyMuPDF page rendering, canonical BoundingBox parsing,
    boundary-clamped coordinate mapping with safety padding, Pillow raster cropping,
    and deterministic reverse-positional Markdown substitution.
    """

    def __init__(self, default_dpi: int = 150, crop_policy: Optional[CropPolicy] = None):
        self.default_dpi = default_dpi
        self.crop_policy = crop_policy or CropPolicy()

    def get_page_count(self, pdf_bytes: bytes) -> int:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return len(doc)

    def render_page_to_jpeg(self, pdf_bytes: bytes, page_number: int, dpi: int = 150) -> bytes:
        dpi_val = dpi or self.default_dpi
        zoom = dpi_val / 72.0
        mat = fitz.Matrix(zoom, zoom)

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            page_idx = page_number - 1
            if page_idx < 0 or page_idx >= len(doc):
                raise IndexError(f"Page index {page_number} out of range (1..{len(doc)})")

            page = doc.load_page(page_idx)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            return pix.tobytes("jpeg")

    def extract_and_crop_images(
        self,
        markdown_text: str,
        page_jpeg_bytes: bytes,
        job_id: int,
        page_number: int = 1,
    ) -> Tuple[str, List[Tuple[str, bytes]]]:
        """
        Parses canonical normalized bounding box tags ([[ymin, xmin, ymax, xmax]]) from markdown,
        maps coordinates to clamped/padded pixel rectangles, crops visual regions via PIL,
        and performs collision-free positional Markdown substitution.

        Guarantees:
          - Page-isolated deterministic artifact names: crop_{job_id}_p{page_number}_{idx}.jpg
          - Reverse-positional text replacement eliminating duplicate/substring collision.
          - Clamped boundaries and configurable safety padding preventing cropped diagram borders.
        """
        if not markdown_text or not page_jpeg_bytes:
            return markdown_text, []

        parsed_matches = BoundingBoxParser.parse_matches(markdown_text)
        if not parsed_matches:
            return markdown_text, []

        try:
            width, height = ImageCropper.get_image_dimensions(page_jpeg_bytes)
        except Exception:
            return markdown_text, []

        cropped_list: List[ExtractedCrop] = []
        replacements: List[Tuple[int, int, str]] = []

        for idx, match_item in enumerate(parsed_matches, start=1):
            pixel_rect = CoordinateMapper.map_to_pixels(
                box=match_item.box,
                image_width=width,
                image_height=height,
                policy=self.crop_policy,
            )

            if pixel_rect is None:
                # Sub-minimum or invalid geometry: skip crop
                continue

            try:
                crop_bytes = ImageCropper.crop_jpeg(
                    image_bytes=page_jpeg_bytes,
                    rect=pixel_rect,
                    quality=self.crop_policy.jpeg_quality,
                )
                filename = f"crop_{job_id}_p{page_number}_{idx}.jpg"
                cropped_list.append(ExtractedCrop(filename=filename, data=crop_bytes, display_order=idx))
                replacements.append((match_item.start, match_item.end, f"![[{filename}]]"))
            except Exception:
                continue

        modified_text = BoundingBoxParser.substitute_positional(markdown_text, replacements)
        return modified_text, cropped_list

    def unify_markdown(self, raw_text: str) -> str:
        """
        Python-level structural Markdown unification:
        - Strips page headers '## صفحه X'
        - Strips separator lines '---'
        - Normalizes consecutive blank lines
        """
        page_header_pattern = re.compile(r"^##\s*صفحه\s*\d+\s*$", re.MULTILINE)
        separator_pattern = re.compile(r"^\s*---\s*$", re.MULTILINE)

        text = page_header_pattern.sub("", raw_text)
        text = separator_pattern.sub("", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def crop_region_image(
        self,
        page_jpeg_bytes: bytes,
        box: BoundingBox,
        policy: Optional[CropPolicy] = None,
    ) -> Optional[bytes]:
        """Crops a specific visual region geometry from a page raster."""
        if not page_jpeg_bytes or not box:
            return None

        pol = policy or self.crop_policy
        try:
            width, height = ImageCropper.get_image_dimensions(page_jpeg_bytes)
        except Exception:
            return None

        pixel_rect = CoordinateMapper.map_to_pixels(
            box=box,
            image_width=width,
            image_height=height,
            policy=pol,
        )
        if pixel_rect is None:
            return None

        try:
            return ImageCropper.crop_jpeg(
                image_bytes=page_jpeg_bytes,
                rect=pixel_rect,
                quality=pol.jpeg_quality,
            )
        except Exception:
            return None

    def get_image_dimensions(self, image_bytes: bytes) -> Tuple[int, int]:
        """Retrieves (width, height) in pixels from raw image bytes."""
        return ImageCropper.get_image_dimensions(image_bytes)
