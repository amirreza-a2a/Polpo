# ============================================================
#  application/ports/document_processor.py
# ============================================================

from abc import ABC, abstractmethod
from typing import List, Tuple


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
    ) -> Tuple[str, List[Tuple[str, bytes]]]:
        """
        استخراج برچسب‌های مختصات [[ymin, xmin, ymax, xmax]] از متن مارک‌داون
        و برش تصاویر مربوطه.
        خروجی: (متن مارک‌داون اصلاح‌شده با تگ ![[filename]], لیست تصاویر برش‌خورده (نام‌فایل, بایت‌ها))
        """
        pass

    @abstractmethod
    def unify_markdown(self, raw_text: str) -> str:
        """یکپارچه‌سازی و حذف سرتیترهای صفحات و جداکننده‌ها از مارک‌داون."""
        pass
