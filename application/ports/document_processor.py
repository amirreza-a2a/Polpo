# ============================================================
#  application/ports/document_processor.py
# ============================================================

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

from core.entities.bounding_box import BoundingBox, CropPolicy


@dataclass(frozen=True)
class ExtractedCrop:
    """
    Represents an extracted visual region crop.
    Carries explicit display_order to enable deterministic region association.
    Supports 2-element tuple unpacking (filename, data) for full backwards compatibility.
    """
    filename: str
    data: bytes
    display_order: int = 1

    def __iter__(self):
        return iter((self.filename, self.data))

    def __getitem__(self, index):
        return (self.filename, self.data)[index]

    def __len__(self):
        return 2


class IDocumentProcessor(ABC):
    """
    درگاه پردازش و رندر اسناد PDF و استخراج بخش‌های تصویر.
    """

    @abstractmethod
    def get_page_count(self, pdf_bytes: bytes) -> int:
        """محاسبه تعداد کل صفحات سند PDF."""
        pass

    @abstractmethod
    def render_page_to_jpeg(self, pdf_bytes: bytes, page_number: int, dpi: int = 150) -> bytes:
        """رندر یک صفحه منفرد PDF به فرمت بایت‌های تصویر JPEG."""
        pass

    @abstractmethod
    def extract_and_crop_images(
        self,
        markdown_text: str,
        page_jpeg_bytes: bytes,
        job_id: int,
        page_number: int = 1,
    ) -> Tuple[str, List[Tuple[str, bytes]]]:
        """
        Extracts coordinate tags [[ymin, xmin, ymax, xmax]] from markdown text,
        crops the corresponding visual regions from the page image, and substitutes
        Returns:
          (modified_markdown_text, List[(filename, cropped_jpeg_bytes)])
        """
        pass

    @abstractmethod
    def unify_markdown(self, raw_text: str) -> str:
        """یکپارچه‌سازی و حذف سرتیترهای صفحات و جداکننده‌ها از مارک‌داون."""
        pass

    @abstractmethod
    def crop_region_image(
        self,
        page_jpeg_bytes: bytes,
        box: "BoundingBox",
        policy: Optional["CropPolicy"] = None,
    ) -> Optional[bytes]:
        """Crops a specific visual region geometry from a page raster."""
        pass
