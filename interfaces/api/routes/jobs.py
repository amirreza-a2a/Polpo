# ============================================================
#  interfaces/api/routes/jobs.py
# ============================================================

import asyncio
import json
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from interfaces.api.deps import get_current_user, get_container
from infrastructure.composition import AppContainer
from application.dto.user_dto import UserDTO
from application.dto.job_dto import SubmitJobCommand, JobResponseDTO, JobDetailDTO
from core.exceptions.domain_exceptions import EntityNotFoundError

router = APIRouter(prefix="/jobs", tags=["Jobs"])


class ResumeJobRequest(BaseModel):
    api_chain_ids: Optional[List[int]] = None


@router.post("", status_code=status.HTTP_201_CREATED, response_model=JobResponseDTO)
async def submit_job(
    file: UploadFile = File(...),
    prompt_id: Optional[int] = Form(None),
    prompt_text: Optional[str] = Form(None),
    api_chain_ids: Optional[str] = Form(None),
    auto_pipeline2: bool = Form(False),
    pipeline2_prompt_id: Optional[int] = Form(None),
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    ارسال و ثبت سند PDF جدید جهت پردازش و تبدیل به مارک‌داون.
    """
    file_bytes = await file.read()
    chain_ids_list = None
    if api_chain_ids:
        try:
            chain_ids_list = json.loads(api_chain_ids)
        except Exception:
            pass

    cmd = SubmitJobCommand(
        user_id=user.id,
        filename=file.filename or "document.pdf",
        file_bytes=file_bytes,
        prompt_id=prompt_id,
        prompt_text=prompt_text,
        api_chain_ids=chain_ids_list,
        auto_pipeline2=auto_pipeline2,
        pipeline2_prompt_id=pipeline2_prompt_id,
    )

    return container.job_submission_service.submit_job(cmd)


@router.get("", response_model=List[JobResponseDTO])
async def list_jobs(
    limit: int = 50,
    offset: int = 0,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    لیست کارهای ثبت‌شده توسط کاربر جاری.
    """
    return container.job_submission_service.list_user_jobs(user.id, limit, offset)


@router.get("/{job_id}", response_model=JobDetailDTO)
async def get_job_detail(
    job_id: int,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    دریافت جزئیات کامل وضعیت کار، پیشرفت صفحات و لاگ سوئیچ اسلات‌ها.
    """
    return container.job_submission_service.get_job_detail(job_id, user.id)


@router.post("/{job_id}/resume", response_model=JobResponseDTO)
async def resume_job(
    job_id: int,
    body: Optional[ResumeJobRequest] = None,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    از سرگیری یک کار متوقف‌شده (Paused/Failed) با همان زنجیره یا زنجیره جدید.
    """
    chain_ids = body.api_chain_ids if body else None
    return container.job_recovery_service.resume_job(job_id, user.id, chain_ids)


@router.post("/{job_id}/retry", response_model=JobResponseDTO)
async def retry_job(
    job_id: int,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    تلاش مجدد برای کار ناموفق بر اساس سیاست Retry.
    """
    return container.job_recovery_service.retry_job(job_id, user.id)


@router.get("/{job_id}/artifacts/{artifact_type}")
async def download_artifact(
    job_id: int,
    artifact_type: str = "output_markdown",
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    دانلود فایل خروجی مارک‌داون یا آرتیفکت‌های سند.
    """
    try:
        stream, filename, mime_type = container.artifact_service.get_job_artifact_stream(
            job_id=job_id,
            user_id=user.id,
            artifact_type=artifact_type,
        )
        return StreamingResponse(
            stream,
            media_type=mime_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except EntityNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: int,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    جریان زنده رویدادهای پیشرفت کار از طریق Server-Sent Events (SSE) برای کلاینت دسکتاپ.
    """
    job_detail = container.job_submission_service.get_job_detail(job_id, user.id)

    async def event_generator():
        q = container.notifier.subscribe(job_id)
        try:
            initial_data = {
                "job_id": job_detail.id,
                "status": job_detail.status,
                "processed_pages": job_detail.processed_pages,
                "total_pages": job_detail.total_pages,
            }
            yield f"event: status\ndata: {json.dumps(initial_data)}\n\n"

            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"event: {payload['event']}\ndata: {json.dumps(payload['data'])}\n\n"
                    if payload["event"] in ("completed", "failed"):
                        break
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            container.notifier.unsubscribe(job_id, q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
