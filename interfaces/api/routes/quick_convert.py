# ============================================================
#  interfaces/api/routes/quick_convert.py
# ============================================================

from typing import Optional
from fastapi import APIRouter, Depends, File, Form, UploadFile
from interfaces.api.deps import get_current_user, get_container
from infrastructure.composition import AppContainer
from application.dto.user_dto import UserDTO
from application.dto.quick_convert_dto import QuickConvertCommand, QuickConvertResultDTO

router = APIRouter(prefix="/quick-convert", tags=["Quick Convert"])


@router.post("", response_model=QuickConvertResultDTO)
async def quick_convert_image(
    image: UploadFile = File(...),
    prompt_text: Optional[str] = Form(None),
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    تبدیل سریع یک تصویر منفرد به مارک‌داون به‌صورت همگام.
    """
    image_bytes = await image.read()
    cmd = QuickConvertCommand(
        user_id=user.id,
        image_bytes=image_bytes,
        prompt_text=prompt_text,
        mime_type=image.content_type or "image/jpeg",
    )
    return container.quick_convert_service.convert_image(cmd)
