# ============================================================
#  interfaces/api/deps.py
# ============================================================

from typing import Optional
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from infrastructure.composition import AppContainer, get_app_container
from application.dto.user_dto import UserDTO
from core.exceptions.domain_exceptions import AuthenticationError

security = HTTPBearer(auto_error=False)


def get_container() -> AppContainer:
    return get_app_container()


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    api_key_header: Optional[str] = Header(None, alias="X-API-Key"),
    container: AppContainer = Depends(get_container),
) -> UserDTO:
    """
    اعتبارسنجی احراز هویت درخواست‌های ورودی.
    الگوی دسکتاپ/سشن: Authorization: Bearer <token>
    الگوی دسترسی ماشین: X-API-Key: polpot_key_<user_id>_<signature>
    """
    if credentials and credentials.scheme.lower() == "bearer":
        try:
            return container.auth_service.authenticate_token(credentials.credentials)
        except AuthenticationError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
                headers={"WWW-Authenticate": "Bearer"},
            )

    if api_key_header:
        try:
            return container.auth_service.authenticate_api_key(api_key_header)
        except AuthenticationError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid X-API-Key: {e}",
                headers={"WWW-Authenticate": "Bearer"},
            )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing Authorization Bearer token or X-API-Key header.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_admin_user(
    current_user: UserDTO = Depends(get_current_user),
) -> UserDTO:
    """
    اعتبارسنجی سطح دسترسی مدیر برای عملیات سیستمی و پنل ادمین.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required for this endpoint.",
        )
    return current_user
