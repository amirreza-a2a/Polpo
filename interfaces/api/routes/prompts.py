# ============================================================
#  interfaces/api/routes/prompts.py
# ============================================================

from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from interfaces.api.deps import get_current_user, get_container
from infrastructure.composition import AppContainer
from application.dto.user_dto import UserDTO
from application.dto.prompt_dto import PromptDTO, CreatePromptCommand, UpdatePromptCommand

router = APIRouter(prefix="/prompts", tags=["Prompts"])


class CreatePromptRequest(BaseModel):
    name: str
    text: str
    prompt_type: str = "pipeline1"
    is_default: bool = False


class UpdatePromptRequest(BaseModel):
    name: Optional[str] = None
    text: Optional[str] = None
    is_default: Optional[bool] = None


@router.get("", response_model=List[PromptDTO])
async def list_prompts(
    prompt_type: Optional[str] = None,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    لیست پرامپت‌های موجود در سیستم.
    """
    return container.prompt_service.list_prompts(prompt_type)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PromptDTO)
async def create_prompt(
    body: CreatePromptRequest,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    """
    ایجاد یک پرامپت سفارشی جدید.
    """
    cmd = CreatePromptCommand(
        name=body.name,
        text=body.text,
        prompt_type=body.prompt_type,
        is_default=body.is_default,
    )
    return container.prompt_service.create_prompt(cmd)


@router.get("/{prompt_id}", response_model=PromptDTO)
async def get_prompt(
    prompt_id: int,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    return container.prompt_service.get_prompt_by_id(prompt_id)


@router.put("/{prompt_id}", response_model=PromptDTO)
async def update_prompt(
    prompt_id: int,
    body: UpdatePromptRequest,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    cmd = UpdatePromptCommand(
        prompt_id=prompt_id,
        name=body.name,
        text=body.text,
        is_default=body.is_default,
    )
    return container.prompt_service.update_prompt(cmd)


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt(
    prompt_id: int,
    user: UserDTO = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
):
    deleted = container.prompt_service.delete_prompt(prompt_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found.")
