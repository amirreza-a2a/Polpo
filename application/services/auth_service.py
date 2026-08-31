# ============================================================
#  application/services/auth_service.py
# ============================================================

from typing import Optional, Tuple
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.token_service import ITokenService
from application.dto.user_dto import UserDTO
from core.entities.user import User, QuotaAllocation, UserPreferences
from core.exceptions.domain_exceptions import AuthenticationError, EntityNotFoundError, DomainError


class AuthService:
    """
    سرویس احراز هویت دسکتاپ، کلیدهای دسترسی ماشین و تبادل کد یک‌بار مصرف تلگرام.
    """

    def __init__(self, uow_factory: IUnitOfWorkFactory, token_service: ITokenService):
        self.uow_factory = uow_factory
        self.token_service = token_service

    def create_telegram_exchange_code(self, user_id: int) -> str:
        with self.uow_factory.create() as uow:
            user = uow.users.get_by_id(user_id)
            if not user:
                raise EntityNotFoundError("User", user_id)
        return self.token_service.create_one_time_exchange_code(user_id)

    def exchange_code_for_token(self, code: str) -> Tuple[str, UserDTO]:
        user_id = self.token_service.exchange_code_for_user_id(code)
        if not user_id:
            raise AuthenticationError("Invalid or expired exchange code.")

        with self.uow_factory.create() as uow:
            user = uow.users.get_by_id(user_id)
            if not user:
                raise AuthenticationError("User associated with exchange code no longer exists.")
            token = self.token_service.create_access_token(user.id)
            return token, self._to_dto(user)

    def authenticate_token(self, token: str) -> UserDTO:
        user_id = self.token_service.verify_access_token(token)
        if not user_id:
            raise AuthenticationError("Invalid or expired bearer token.")

        with self.uow_factory.create() as uow:
            user = uow.users.get_by_id(user_id)
            if not user:
                raise AuthenticationError("User not found.")
            return self._to_dto(user)

    def authenticate_api_key(self, api_key: str) -> UserDTO:
        user_id = self.token_service.verify_machine_api_key(api_key)
        if not user_id:
            raise AuthenticationError("Invalid or unauthorized machine API key.")

        with self.uow_factory.create() as uow:
            user = uow.users.get_by_id(user_id)
            if not user:
                raise AuthenticationError("User not found for machine API key.")
            return self._to_dto(user)

    def create_machine_api_key(self, user_id: int) -> str:
        with self.uow_factory.create() as uow:
            user = uow.users.get_by_id(user_id)
            if not user:
                raise EntityNotFoundError("User", user_id)
        return self.token_service.create_machine_api_key(user_id)

    def register_desktop_user(self, username: str) -> Tuple[str, UserDTO]:
        clean_username = username.strip().lower()
        if not clean_username:
            raise DomainError("Username cannot be empty.")

        with self.uow_factory.create() as uow:
            existing = uow.users.get_by_username(clean_username)
            if existing:
                raise DomainError(f"Username '{clean_username}' is already registered. Cannot re-register existing account.")

            user = User(
                id=None,
                telegram_id=None,
                username=clean_username,
                is_admin=False,
                quota=QuotaAllocation(daily_limit=50, daily_pages_used=0),
                preferences=UserPreferences(),
            )
            saved_user = uow.users.save(user)
            uow.commit()

            token = self.token_service.create_access_token(saved_user.id)
            return token, self._to_dto(saved_user)

    def _to_dto(self, user: User) -> UserDTO:
        return UserDTO(
            id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            is_admin=user.is_admin,
            daily_pages_used=user.quota.daily_pages_used,
            daily_limit=user.quota.daily_limit,
            remaining_pages=user.quota.remaining_pages,
            use_public_fallback=user.preferences.use_public_fallback,
            auto_retry=user.preferences.auto_retry,
            auto_pipeline2=user.preferences.auto_pipeline2,
            default_prompt_id=user.preferences.default_prompt_id,
            default_pipeline2_prompt_id=user.preferences.default_pipeline2_prompt_id,
        )
