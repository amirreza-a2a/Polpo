# ============================================================
#  services/pipeline2_processor.py
#  پایپ‌لاین ۲: یکپارچه‌سازی Python-side + پردازش AI روی متن
# ============================================================

import re
import os
from pathlib import Path

from services.api_manager import get_default_model
from utils.file_manager import get_pipeline2_dir


# ─── الگوهای حذف هنگام یکپارچه‌سازی ──────────────────────
PAGE_HEADER_PATTERN = re.compile(r'^##\s*صفحه\s*\d+\s*$', re.MULTILINE)
SEPARATOR_PATTERN    = re.compile(r'^\s*---\s*$', re.MULTILINE)


def unify_markdown(raw_text: str) -> str:
    """
    یکپارچه‌سازی سطح Python (بدون AI):
    - حذف سرتیترهای "## صفحه X"
    - حذف خطوط جداکننده "---"
    - حذف خطوط خالی اضافی
    """
    text = PAGE_HEADER_PATTERN.sub('', raw_text)
    text = SEPARATOR_PATTERN.sub('', text)

    # حذف بیش از دو خط خالی پشت‌سرهم
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_pipeline2_input_path(p2_job_id: int) -> str:
    d = get_pipeline2_dir(p2_job_id)
    return os.path.join(d, "unified_input.md")


def get_pipeline2_output_path(p2_job_id: int, source_file_name: str) -> str:
    d = get_pipeline2_dir(p2_job_id)
    base = os.path.splitext(source_file_name)[0]
    return os.path.join(d, f"unified_{base}.md")


def prepare_unified_input(p2_job_id: int, source_md_path: str) -> str:
    """
    فایل Markdown خام pipeline1 را می‌خواند، یکپارچه می‌کند،
    و در مسیر input pipeline2 ذخیره می‌کند.
    """
    with open(source_md_path, "r", encoding="utf-8") as f:
        raw = f.read()

    unified = unify_markdown(raw)

    input_path = get_pipeline2_input_path(p2_job_id)
    Path(input_path).parent.mkdir(parents=True, exist_ok=True)
    with open(input_path, "w", encoding="utf-8") as f:
        f.write(unified)

    return input_path


def _call_ai_text(text: str, prompt_text: str, api_entry: dict) -> str:
    """
    متن یکپارچه را همراه با prompt به AI می‌فرستد (فقط متن — بدون تصویر).
    خطاهای AIError بالا پرتاب می‌شوند تا در حلقه پردازش مدیریت شوند.
    """
    from core.ai.types import TextPromptRequest
    from services.ai_executor import execute_single_text_request
    full_prompt = f"{prompt_text}\n\n---\n\nمتن سند:\n\n{text}"
    req = TextPromptRequest(prompt=full_prompt, model=api_entry.get("selected_model"))
    response = execute_single_text_request(req, api_entry)
    return response.content


def process_pipeline2_job(p2_job: dict, source_job: dict,
                          prompt_text: str,
                          notify_switch_callback=None) -> bool:
    """
    یک pipeline2_job را پردازش می‌کند:
    ① آماده‌سازی ورودی یکپارچه (اگر هنوز آماده نشده)
    ② ارسال به AI با prompt انتخابی
    ③ ذخیره خروجی
    """
    from core.ai.exceptions import AIError
    from services.api_manager import switch_to_next_api, report_pages_used
    from database.models import update_pipeline2_job_status

    p2_id = p2_job["id"]

    # ─── ① آماده‌سازی ورودی (فقط بار اول) ──────────────────
    input_path = p2_job.get("input_path")
    if not input_path or not os.path.exists(input_path):
        source_md = source_job["output_path"]
        if not source_md or not os.path.exists(source_md):
            update_pipeline2_job_status(p2_id, "failed", "فایل Markdown اصلی یافت نشد.")
            return False
        input_path = prepare_unified_input(p2_id, source_md)
        from database.models import update_pipeline2_job_paths
        update_pipeline2_job_paths(p2_id, input_path=input_path)

    with open(input_path, "r", encoding="utf-8") as f:
        unified_text = f.read()

    # ─── ② ارسال به AI ──────────────────────────────────────
    current_api = p2_job["api_chain"][p2_job["current_api_index"]]
    result = None
    try:
        result = _call_ai_text(unified_text, prompt_text, current_api)
    except AIError as ai_err:
        print(f"  🔄 خطای هوش مصنوعی در pipeline2: {ai_err}")
        result = None

    if result is None:
        old_label = current_api["label"]
        new_api   = switch_to_next_api(p2_job, "api_error", at_page=0)

        if new_api is None:
            update_pipeline2_job_status(
                p2_id, "paused",
                "همه API‌های موجود ناموفق بودند."
            )
            return False

        current_api = new_api
        if notify_switch_callback:
            notify_switch_callback(p2_job["user_id"], old_label, current_api["label"])

        try:
            result = _call_ai_text(unified_text, prompt_text, current_api)
        except AIError as ai_err:
            print(f"  🔄 خطای هوش مصنوعی با API جدید در pipeline2: {ai_err}")
            result = None

        if result is None:
            update_pipeline2_job_status(
                p2_id, "failed",
                "پردازش با API جدید هم ناموفق بود."
            )
            return False

    # ─── ③ ذخیره خروجی ──────────────────────────────────────
    output_path = get_pipeline2_output_path(p2_id, source_job["file_name"])
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)

    from database.models import update_pipeline2_job_paths
    update_pipeline2_job_paths(p2_id, output_path=output_path)
    report_pages_used(current_api, 1)

    return True