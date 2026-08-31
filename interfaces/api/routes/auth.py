# ============================================================
#  interfaces/api/routes/auth.py
# ============================================================

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from interfaces.api.deps import get_container
from infrastructure.composition import AppContainer
from core.exceptions.domain_exceptions import AuthenticationError

router = APIRouter(prefix="/auth", tags=["Authentication"])


class ExchangeRequest(BaseModel):
    code: str


class RegisterRequest(BaseModel):
    username: str


@router.post("/exchange")
async def exchange_telegram_code(
    body: ExchangeRequest,
    container: AppContainer = Depends(get_container),
):
    """
    تبادل کد یک‌بار مصرف تلگرام با Bearer Access Token برای کلاینت دسکتاپ.
    """
    try:
        token, user = container.auth_service.exchange_code_for_token(body.code)
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": user,
        }
    except AuthenticationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/register")
async def register_standalone_user(
    body: RegisterRequest,
    container: AppContainer = Depends(get_container),
):
    """
    ورود یا ثبت‌نام مستقل کاربر بدون نیاز به تلگرام.
    """
    token, user = container.auth_service.register_or_login_desktop_user(body.username)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user,
    }
