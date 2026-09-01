# ============================================================
#  interfaces/api/routes/admin.py
# ============================================================

from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from interfaces.api.deps import get_current_admin_user, get_container
from infrastructure.composition import AppContainer
from application.dto.user_dto import UserDTO
from application.dto.api_dto import ApiSlotDTO

router = APIRouter(prefix="/admin", tags=["Admin Panel"])


class ApproveDonationRequest(BaseModel):
    selected_model: Optional[str] = None
    base_url: Optional[str] = None


class RegisterPublicApiRequest(BaseModel):
    provider: str
    api_key: str
    label: str
    models: List[str]
    daily_limit: int = 200
    selected_model: Optional[str] = None
    base_url: Optional[str] = None


class UpdatePublicApiRequest(BaseModel):
    is_active: Optional[bool] = None
    selected_model: Optional[str] = None
    base_url: Optional[str] = None


@router.get("/stats")
async def get_system_stats(
    admin: UserDTO = Depends(get_current_admin_user),
    container: AppContainer = Depends(get_container),
):
    """
    دریافت آمار کلی سیستم، تعداد جاب‌ها و صفحات پردازش‌شده امروز برای داشبورد ادمین.
    """
    return container.job_query_service.get_today_stats()


@router.get("/donations")
async def list_api_donations(
    admin: UserDTO = Depends(get_current_admin_user),
    container: AppContainer = Depends(get_container),
):
    """
    لیست کلیدهای اهدایی کاربران و وضعیت تایید/رد آنها.
    """
    return container.api_service.list_donations()


@router.post("/donations/{donation_id}/approve", response_model=ApiSlotDTO)
async def approve_donation(
    donation_id: int,
    body: Optional[ApproveDonationRequest] = None,
    admin: UserDTO = Depends(get_current_admin_user),
    container: AppContainer = Depends(get_container),
):
    """
    تایید کلید اهدایی و اضافه کردن آن به استخر کلیدهای عمومی بات.
    """
    selected_model = body.selected_model if body else None
    base_url = body.base_url if body else None
    slot = container.api_service.approve_donation(
        donation_id=donation_id,
        selected_model=selected_model,
        base_url=base_url,
    )
    if not slot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Donation record not found.",
        )
    return slot


@router.post("/donations/{donation_id}/reject", status_code=status.HTTP_200_OK)
async def reject_donation(
    donation_id: int,
    admin: UserDTO = Depends(get_current_admin_user),
    container: AppContainer = Depends(get_container),
):
    """
    رد کلید اهدایی.
    """
    rejected = container.api_service.reject_donation(donation_id)
    if not rejected:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Donation record not found.",
        )
    return {"message": "Donation marked as rejected."}


@router.get("/public-apis", response_model=List[ApiSlotDTO])
async def list_public_apis(
    admin: UserDTO = Depends(get_current_admin_user),
    container: AppContainer = Depends(get_container),
):
    """
    لیست تمام کلیدهای عمومی سیستم.
    """
    return container.api_service.list_public_apis()


@router.post("/public-apis", status_code=status.HTTP_201_CREATED, response_model=ApiSlotDTO)
async def register_public_api(
    body: RegisterPublicApiRequest,
    admin: UserDTO = Depends(get_current_admin_user),
    container: AppContainer = Depends(get_container),
):
    """
    ثبت مستقیم کلید عمومی توسط مدیر سیستم.
    """
    return container.api_service.register_public_api(
        provider=body.provider,
        api_key=body.api_key,
        label=body.label,
        models=body.models,
        daily_limit=body.daily_limit,
        selected_model=body.selected_model,
        base_url=body.base_url,
    )


@router.patch("/public-apis/{api_id}")
async def update_public_api(
    api_id: int,
    body: UpdatePublicApiRequest,
    admin: UserDTO = Depends(get_current_admin_user),
    container: AppContainer = Depends(get_container),
):
    """
    فعال/غیرفعال‌سازی یا تغییر مدل و Base URL کلید عمومی.
    """
    if body.is_active is not None:
        container.api_service.toggle_public_api(api_id, body.is_active)
    if body.selected_model is not None or body.base_url is not None:
        container.api_service.update_public_api_model_url(
            api_id, body.selected_model, body.base_url
        )
    return {"message": "Public API updated successfully."}


@router.delete("/public-apis/{api_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_public_api(
    api_id: int,
    admin: UserDTO = Depends(get_current_admin_user),
    container: AppContainer = Depends(get_container),
):
    """
    حذف کلید عمومی از سیستم.
    """
    deleted = container.api_service.delete_public_api(api_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Public API slot not found.",
        )
