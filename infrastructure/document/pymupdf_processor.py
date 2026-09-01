# ============================================================
#  infrastructure/document/pymupdf_processor.py
# ============================================================

import io
import re
from typing import List, Tuple
try:
    import pymupdf as fitz
except ImportError:
    import fitz
from PIL import Image
from application.ports.document_processor import IDocumentProcessor


class PyMuPDFDocumentProcessor(IDocumentProcessor):
    """
    پیاده‌سازی درگاه پردازش اسناد PDF و برش تصاویر با استفاده از PyMuPDF و Pillow.
    """

    def __init__(self, default_dpi: int = 150):
        self.default_dpi = default_dpi

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
    ) -> Tuple[str, List[Tuple[str, bytes]]]:
        """
        تشخیص برچسب‌های مختصات [[ymin, xmin, ymax, xmax]] در متن و برش تصویر مربوطه.
        مختصات بر پایه مقیاس 0 تا 1000 است.
        """
        pattern = r"\[\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]\]"
        matches = list(re.finditer(pattern, markdown_text))
        if not matches:
            return markdown_text, []

        img = Image.open(io.BytesIO(page_jpeg_bytes))
        width, height = img.size
        cropped_list: List[Tuple[str, bytes]] = []

        modified_text = markdown_text
        for idx, match in enumerate(matches, start=1):
            try:
                ymin, xmin, ymax, xmax = map(int, match.groups())
                # تبدیل مقیاس 1000 به پیکسل واقعی
                left = int(xmin * width / 1000.0)
                top = int(ymin * height / 1000.0)
                right = int(xmax * width / 1000.0)
                bottom = int(ymax * height / 1000.0)

                # اعتبارسنجی ابعاد
                if right > left and bottom > top:
                    crop = img.crop((left, top, right, bottom))
                    buf = io.BytesIO()
                    crop.save(buf, format="JPEG", quality=90)
                    filename = f"crop_{job_id}_{idx}.jpg"
                    cropped_list.append((filename, buf.getvalue()))

                    # جایگزینی تگ در متن
                    modified_text = modified_text.replace(match.group(0), f"![[{filename}]]", 1)
            except Exception:
                continue

        return modified_text, cropped_list

    def unify_markdown(self, raw_text: str) -> str:
        """
        یکپارچه‌سازی سطح Python (بدون AI):
        - حذف سرتیترهای "## صفحه X"
        - حذف خطوط جداکننده "---"
        - حذف خطوط خالی اضافی
        """
        page_header_pattern = re.compile(r"^##\s*صفحه\s*\d+\s*$", re.MULTILINE)
        separator_pattern = re.compile(r"^\s*---\s*$", re.MULTILINE)

        text = page_header_pattern.sub("", raw_text)
        text = separator_pattern.sub("", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
