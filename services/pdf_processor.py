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
from services.api_manager import switch_to_next_api, report_pages_used, get_default_model
from database.models import update_job_progress, update_job_status
from utils.file_manager import get_attachments_dir, get_output_path


# ─── الگوی مختصات در متن markdown ───────────────────────
COORD_PATTERN = re.compile(r'\[\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]\]')


def extract_and_crop_images(markdown_text: str, pil_img: Image.Image,
                             attachments_dir: str) -> str:
    width, height = pil_img.size
    matches = list(COORD_PATTERN.finditer(markdown_text))

    if not matches:
        return markdown_text

    for match in reversed(matches):
        try:
            ymin, xmin, ymax, xmax = map(int, match.groups())

            left   = (xmin * width)  / 1000
            top    = (ymin * height) / 1000
            right  = (xmax * width)  / 1000
            bottom = (ymax * height) / 1000

            padding  = 10
            crop_box = (
                max(0, left   - padding),
                max(0, top    - padding),
                min(width,  right  + padding),
                min(height, bottom + padding),
            )

            cropped = pil_img.crop(crop_box)

            file_id  = int(time.time() * 1000)
            filename = f"image_{file_id}.jpg"
            save_path = os.path.join(attachments_dir, filename)

            if cropped.mode in ("RGBA", "P"):
                cropped = cropped.convert("RGB")
            cropped.save(save_path, "JPEG", quality=95)

            markdown_text = (
                markdown_text[:match.start()]
                + f"![[{filename}]]"
                + markdown_text[match.end():]
            )

            time.sleep(0.01)

        except Exception as e:
            print(f"    ⚠️ خطا در crop تصویر: {e}")

    return markdown_text


def _process_single_page(pil_img: Image.Image, prompt: str,
                          api_entry: dict) -> str | None:
    """
    یک صفحه را به API می‌فرستد و متن markdown خام را برمی‌گرداند.
    از selected_model و base_url موجود در api_entry استفاده می‌کند.
    """
    api_key  = api_entry["api_key"]
    provider = api_entry["provider"]
    model    = api_entry.get("selected_model") or get_default_model(provider)
    base_url = api_entry.get("base_url")

    # ─── Google / Gemini ──────────────────────────────────
    if provider == "google":
        try:
            client   = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model    = model,
                contents = [pil_img, prompt],
            )
            return response.text
        except Exception as e:
            print(f"    ⚠️ Google API error (model={model}): {e}")
            return None

    # ─── OpenAI / OpenRouter / Custom ────────────────────
    if provider in ("openai", "openrouter") or base_url:
        try:
            import openai as openai_lib
            import base64

            # تبدیل تصویر PIL به base64
            buf = io.BytesIO()
            if pil_img.mode in ("RGBA", "P"):
                pil_img = pil_img.convert("RGB")
            pil_img.save(buf, format="JPEG", quality=90)
            img_b64 = base64.b64encode(buf.getvalue()).decode()

            client_kwargs = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url

            client = openai_lib.OpenAI(**client_kwargs)

            response = client.chat.completions.create(
                model    = model,
                messages = [{
                    "role": "user",
                    "content": [
                        {
                            "type":      "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            return response.choices[0].message.content

        except Exception as e:
            print(f"    ⚠️ {provider} API error (model={model}): {e}")
            return None

    print(f"    ⚠️ provider ناشناخته: {provider}")
    return None


def process_job(job: dict, prompt_text: str,
                notify_switch_callback=None) -> bool:
    job_id      = job["id"]
    file_path   = job["file_path"]
    output_path = job.get("output_path") or get_output_path(job_id)
    switch_log  = job.get("api_switch_log") or []

    attachments_dir = get_attachments_dir(job_id)

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        update_job_status(job_id, "failed", f"خطا در باز کردن PDF: {e}")
        return False

    total_pages     = len(doc)
    processed_pages = job.get("processed_pages") or 0
    current_api     = job["api_chain"][job["current_api_index"]]

    write_mode = "a" if processed_pages > 0 else "w"
    if write_mode == "w":
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# {job['file_name']}\n\n")

    for page_num in range(processed_pages, total_pages):
        page    = doc.load_page(page_num)
        pix     = page.get_pixmap(dpi=DPI)
        pil_img = Image.open(io.BytesIO(pix.tobytes("png")))

        model_label = current_api.get("selected_model", "?")
        print(f"  📄 صفحه {page_num + 1}/{total_pages} | API: {current_api['label']} | مدل: {model_label}")

        wait_if_needed(current_api)

        result = _process_single_page(pil_img, prompt_text, current_api)
        mark_request_sent(current_api)

        if result is None:
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

            wait_if_needed(current_api)
            result = _process_single_page(pil_img, prompt_text, current_api)
            mark_request_sent(current_api)

            if result is None:
                update_job_status(
                    job_id, "failed",
                    f"پردازش صفحه {page_num + 1} با API جدید هم ناموفق بود.",
                )
                doc.close()
                return False

        result = extract_and_crop_images(result, pil_img, attachments_dir)

        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"## صفحه {page_num + 1}\n\n{result}\n\n---\n\n")

        processed_pages += 1
        report_pages_used(current_api, 1)

        update_job_progress(
            job_id            = job_id,
            processed_pages   = processed_pages,
            current_api_index = job["current_api_index"],
            switch_log        = job.get("api_switch_log") or [],
            output_path       = output_path,
        )

    doc.close()
    return True