# ============================================================
#  services/pdf_processor.py  –  موتور پردازش PDF
# ============================================================

import io
import re
import time
import os
from pathlib import Path

import fitz                        # PyMuPDF
from PIL import Image

from google import genai

from config import DPI
from utils.rate_limiter import wait_if_needed, mark_request_sent
from services.api_manager import switch_to_next_api, report_pages_used
from database.models import update_job_progress, update_job_status
from utils.file_manager import get_attachments_dir


# ─── الگوی مختصات در متن markdown ───────────────────────
COORD_PATTERN = re.compile(r'\[\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]\]')


# ─── crop و جایگزینی مختصات با اسم عکس ──────────────────

def extract_and_crop_images(markdown_text: str, pil_img: Image.Image,
                             attachments_dir: str) -> str:
    """
    مختصات [[ymin, xmin, ymax, xmax]] را در متن پیدا می‌کند،
    آن ناحیه را از تصویر crop می‌کند،
    در attachments_dir ذخیره می‌کند،
    و مختصات را با ![[image_xxx.jpg]] جایگزین می‌کند.
    """
    width, height = pil_img.size
    matches = list(COORD_PATTERN.finditer(markdown_text))

    if not matches:
        return markdown_text

    # از آخر به اول جایگزین می‌کنیم تا ایندکس‌ها به هم نریزد
    for match in reversed(matches):
        try:
            ymin, xmin, ymax, xmax = map(int, match.groups())

            # تبدیل مختصات 0-1000 به پیکسل واقعی
            left   = (xmin * width)  / 1000
            top    = (ymin * height) / 1000
            right  = (xmax * width)  / 1000
            bottom = (ymax * height) / 1000

            # padding برای کمی فضای اضافه دور تصویر
            padding  = 10
            crop_box = (
                max(0, left   - padding),
                max(0, top    - padding),
                min(width,  right  + padding),
                min(height, bottom + padding),
            )

            cropped = pil_img.crop(crop_box)

            # نام یونیک با timestamp میلی‌ثانیه
            file_id  = int(time.time() * 1000)
            filename = f"image_{file_id}.jpg"
            save_path = os.path.join(attachments_dir, filename)

            if cropped.mode in ("RGBA", "P"):
                cropped = cropped.convert("RGB")
            cropped.save(save_path, "JPEG", quality=95)

            # جایگزینی در متن
            markdown_text = (
                markdown_text[:match.start()]
                + f"![[{filename}]]"
                + markdown_text[match.end():]
            )

            time.sleep(0.01)  # جلوگیری از تشابه timestamp

        except Exception as e:
            print(f"    ⚠️ خطا در crop تصویر: {e}")

    return markdown_text


# ─── پردازش یک صفحه ──────────────────────────────────────

def _process_single_page(pil_img: Image.Image, prompt: str,
                          api_entry: dict) -> str | None:
    """
    یک صفحه را به API می‌فرستد و متن markdown خام را برمی‌گرداند.
    در صورت خطا None برمی‌گرداند.
    """
    api_key  = api_entry["api_key"]
    provider = api_entry["provider"]

    if provider == "google":
        try:
            client   = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model    = "gemini-2.5-flash",
                contents = [pil_img, prompt],
            )
            return response.text
        except Exception as e:
            print(f"    ⚠️ Google API error: {e}")
            return None

    # TODO: پشتیبانی OpenAI / OpenRouter در آینده
    return None


# ─── پردازش کامل یک جاب ─────────────────────────────────

def process_job(job: dict, prompt_text: str,
                notify_switch_callback=None) -> bool:
    """
    کل PDF مربوط به یک جاب را پردازش می‌کند.
    خروجی: True اگر موفق، False اگر ناموفق
    """
    job_id      = job["id"]
    file_path   = job["file_path"]
    output_path = job.get("output_path") or f"output_files/job_{job_id}/output.md"
    switch_log  = job.get("api_switch_log") or []

    # پوشه attachments
    attachments_dir = get_attachments_dir(job_id)

    # ─── باز کردن PDF ──────────────────────────────────────
    try:
        doc = fitz.open(file_path)
    except Exception as e:
        update_job_status(job_id, "failed", f"خطا در باز کردن PDF: {e}")
        return False

    total_pages     = len(doc)
    processed_pages = job.get("processed_pages") or 0
    current_api     = job["api_chain"][job["current_api_index"]]

    # ─── آماده کردن فایل خروجی ────────────────────────────
    write_mode = "a" if processed_pages > 0 else "w"
    if write_mode == "w":
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# {job['file_name']}\n\n")

    # ─── پردازش صفحه به صفحه ─────────────────────────────
    for page_num in range(processed_pages, total_pages):
        page    = doc.load_page(page_num)
        pix     = page.get_pixmap(dpi=DPI)
        pil_img = Image.open(io.BytesIO(pix.tobytes("png")))

        print(f"  📄 صفحه {page_num + 1}/{total_pages} | API: {current_api['label']}")

        # ─── رعایت RPM ────────────────────────────────────
        wait_if_needed(current_api["id"], current_api["provider"])

        # ─── ارسال به API ──────────────────────────────────
        result = _process_single_page(pil_img, prompt_text, current_api)
        mark_request_sent(current_api["id"])

        if result is None:
            # سوئیچ API
            print(f"  🔄 سوئیچ API در صفحه {page_num + 1}...")
            old_label = current_api["label"]
            new_api   = switch_to_next_api(job, "api_error", page_num + 1)

            if new_api is None:
                update_job_status(
                    job_id, "paused",
                    f"همه API‌ها در صفحه {page_num + 1} تمام شدند.",
                )
                doc.close()
                return False

            current_api = new_api
            if notify_switch_callback:
                notify_switch_callback(job["user_id"], old_label, current_api["label"])

            wait_if_needed(current_api["id"], current_api["provider"])
            result = _process_single_page(pil_img, prompt_text, current_api)
            mark_request_sent(current_api["id"])

            if result is None:
                update_job_status(
                    job_id, "failed",
                    f"پردازش صفحه {page_num + 1} با API جدید هم ناموفق بود.",
                )
                doc.close()
                return False

        # ─── crop تصاویر و جایگزینی مختصات ───────────────
        result = extract_and_crop_images(result, pil_img, attachments_dir)

        # ─── ذخیره فوری صفحه ─────────────────────────────
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"## صفحه {page_num + 1}\n\n{result}\n\n---\n\n")

        processed_pages += 1
        report_pages_used(current_api, 1)

        # ─── ذخیره پیشرفت در DB ───────────────────────────
        update_job_progress(
            job_id            = job_id,
            processed_pages   = processed_pages,
            current_api_index = job["current_api_index"],
            switch_log        = job.get("api_switch_log") or [],
            output_path       = output_path,
        )

    doc.close()
    return True
