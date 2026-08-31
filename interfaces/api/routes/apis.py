# ============================================================
#  interfaces/api/routes/apis.py
# ============================================================

from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from interfaces.api.deps import get_current_user, get_container
from infrastructure.composition import AppContainer
from application.dto.user_dto import UserDTO
from application.dto.api_dto import ApiSlotDTO, RegisterApiCommand, DonateApiCommand

router = APIRouter(prefix="/apis", tags=["API Slots"])


class RegisterApiRequest(BaseModel):
    provider: str
    api_key: str
    label: str
    selected_model: Optional[str] = None
    base_url: Optional[str] = None


class DonateApiRequest(BaseModel):
    provider: str
    api_key: str
    label: str
    models: List[str]


@router.get("", response_model=List[ApiSlotDTO])
async def list_user_apis(
    include_public: bool = True,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    لیست کلیدهای API خصوصی کاربر و اسلات‌های عمومی فعال.
    """
    return container.api_service.list_user_apis(user.id, include_public=include_public)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ApiSlotDTO)
async def register_private_api(
    body: RegisterApiRequest,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    ثبت یک کلید API خصوصی جدید برای کاربر جاری.
    """
    cmd = RegisterApiCommand(
        user_id=user.id,
        provider=body.provider,
        api_key=body.api_key,
        label=body.label,
        selected_model=body.selected_model,
        base_url=body.base_url,
    )
    return container.api_service.register_private_api(cmd)


@router.delete("/{api_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_private_api(
    api_id: int,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    deleted = container.api_service.delete_private_api(api_id, user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API slot not found or not owned by user.")


@router.post("/donate", status_code=status.HTTP_201_CREATED)
async def donate_public_api(
    body: DonateApiRequest,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    اهدای کلید API برای استفاده در استخر عمومی بات.
    """
    cmd = DonateApiCommand(
        user_id=user.id,
        provider=body.provider,
        api_key=body.api_key,
        label=body.label,
        models=body.models,
    )
    donation_id = container.api_service.donate_api(cmd)
    return {"message": "Donation registered successfully.", "donation_id": donation_id}
