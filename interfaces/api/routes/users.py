# ============================================================
#  interfaces/api/routes/users.py
# ============================================================

from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends
from interfaces.api.deps import get_current_user, get_container
from infrastructure.composition import AppContainer
from application.dto.user_dto import UserDTO, UpdatePreferencesCommand

router = APIRouter(prefix="/users", tags=["Users"])


class PreferencesUpdateRequest(BaseModel):
    use_public_fallback: Optional[bool] = None
    auto_retry: Optional[bool] = None
    auto_pipeline2: Optional[bool] = None
    default_prompt_id: Optional[int] = None
    default_pipeline2_prompt_id: Optional[int] = None


@router.get("/me", response_model=UserDTO)
async def get_current_user_profile(
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    دریافت پروفایل کاربر جاری، وضعیت سهمیه روزانه و ترجیحات.
    """
    return container.user_service.get_user_by_id(user.id)


@router.patch("/me/preferences", response_model=UserDTO)
async def update_preferences(
    body: PreferencesUpdateRequest,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    به‌روزرسانی تنظیمات کاربر جاری.
    """
    cmd = UpdatePreferencesCommand(
        user_id=user.id,
        use_public_fallback=body.use_public_fallback,
        auto_retry=body.auto_retry,
        auto_pipeline2=body.auto_pipeline2,
        default_prompt_id=body.default_prompt_id,
        default_pipeline2_prompt_id=body.default_pipeline2_prompt_id,
    )
    return container.user_service.update_preferences(cmd)
