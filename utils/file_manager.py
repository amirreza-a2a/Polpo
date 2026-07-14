# ============================================================
#  utils/file_manager.py  –  مدیریت فایل‌های موقت و خروجی
# ============================================================

import os
import shutil
import zipfile
from pathlib import Path
from config import TEMP_DIR, OUTPUT_DIR


def ensure_dirs():
    Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)


def get_temp_path(filename: str) -> str:
    ensure_dirs()
    return os.path.join(TEMP_DIR, filename)


def get_job_dir(job_id: int) -> str:
    """پوشه اصلی یک جاب را برمی‌گرداند و می‌سازد."""
    path = os.path.join(OUTPUT_DIR, f"job_{job_id}")
    Path(path).mkdir(parents=True, exist_ok=True)
    return path




def get_pipeline2_dir(p2_job_id: int) -> str:
    """مسیر پوشه‌ی اختصاصی pipeline2_job را برمی‌گرداند و می‌سازد."""
    d = os.path.join("output_files", "pipeline2", f"job_{p2_job_id}")
    os.makedirs(d, exist_ok=True)
    return d
 


def get_output_path(job_id: int) -> str:
    """مسیر فایل MD خروجی را برمی‌گرداند."""
    return os.path.join(get_job_dir(job_id), "output.md")


def get_attachments_dir(job_id: int) -> str:
    """پوشه attachments یک جاب را برمی‌گرداند و می‌سازد."""
    path = os.path.join(get_job_dir(job_id), "attachments")
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def get_zip_path(job_id: int) -> str:
    """مسیر فایل zip خروجی attachments را برمی‌گرداند."""
    return os.path.join(get_job_dir(job_id), "attachments.zip")


def has_attachments(job_id: int) -> bool:
    """آیا این جاب عکسی دارد؟"""
    attachments_dir = os.path.join(OUTPUT_DIR, f"job_{job_id}", "attachments")
    if not os.path.exists(attachments_dir):
        return False
    files = [f for f in os.listdir(attachments_dir) if f.endswith((".jpg", ".png", ".jpeg"))]
    return len(files) > 0


def create_attachments_zip(job_id: int) -> str | None:
    """
    پوشه attachments را zip می‌کند.
    مسیر فایل zip را برمی‌گرداند یا None اگر عکسی نباشد.
    """
    if not has_attachments(job_id):
        return None

    attachments_dir = get_attachments_dir(job_id)
    zip_path        = get_zip_path(job_id)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename in os.listdir(attachments_dir):
            if filename.endswith((".jpg", ".png", ".jpeg")):
                zf.write(
                    os.path.join(attachments_dir, filename),
                    arcname=filename,
                )

    print(f"📦 ZIP ساخته شد: {zip_path}")
    return zip_path


def delete_file(path: str):
    """یک فایل را حذف می‌کند (بدون خطا اگر وجود نداشته باشد)."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def cleanup_job_files(job: dict):
    """تمام فایل‌های موقت و خروجی مربوط به یک جاب را حذف می‌کند."""
    # فایل PDF اصلی
    delete_file(job.get("file_path"))

    # پوشه کامل job (MD + attachments + zip)
    job_dir = os.path.join(OUTPUT_DIR, f"job_{job['id']}")
    try:
        if os.path.exists(job_dir):
            shutil.rmtree(job_dir)
    except Exception as e:
        print(f"⚠️ خطا در حذف پوشه جاب {job['id']}: {e}")


def get_file_size_mb(path: str) -> float:
    if not os.path.exists(path):
        return 0.0
    return os.path.getsize(path) / (1024 * 1024)
