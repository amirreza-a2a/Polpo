# ============================================================
#  utils/file_manager.py  –  Temporary and output file management (legacy)
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
    """Return and create the main directory for a job."""
    path = os.path.join(OUTPUT_DIR, f"job_{job_id}")
    Path(path).mkdir(parents=True, exist_ok=True)
    return path




def get_pipeline2_dir(p2_job_id: int) -> str:
    """Return and create the dedicated directory for a pipeline2_job."""
    d = os.path.join(OUTPUT_DIR, "pipeline2", f"job_{p2_job_id}")
    os.makedirs(d, exist_ok=True)
    return d
 


def get_output_path(job_id: int) -> str:
    """Return the path to the output Markdown file."""
    return os.path.join(get_job_dir(job_id), "output.md")


def get_attachments_dir(job_id: int) -> str:
    """Return and create the attachments directory for a job."""
    path = os.path.join(get_job_dir(job_id), "attachments")
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def get_zip_path(job_id: int) -> str:
    """Return the path to the attachments zip file."""
    return os.path.join(get_job_dir(job_id), "attachments.zip")


def has_attachments(job_id: int) -> bool:
    """Check whether this job has image attachments."""
    attachments_dir = os.path.join(OUTPUT_DIR, f"job_{job_id}", "attachments")
    if not os.path.exists(attachments_dir):
        return False
    files = [f for f in os.listdir(attachments_dir) if f.endswith((".jpg", ".png", ".jpeg"))]
    return len(files) > 0


def create_attachments_zip(job_id: int) -> str | None:
    """
    Compress the attachments directory into a zip file.
    Returns the zip file path, or None if no attachments exist.
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
    """Delete a file safely (no-op if it does not exist)."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def cleanup_temp_input(job: dict):
    """Delete the temporary input PDF file while retaining the output directory."""
    delete_file(job.get("file_path"))


def cleanup_job_files(job: dict):
    """Delete all temporary and output files associated with a job."""
    # Original PDF file
    delete_file(job.get("file_path"))

    # Complete job directory (MD + attachments + zip)
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
