# ============================================================
#  application/services/user_service.py
# ============================================================

from datetime import date
from typing import Optional
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.dto.user_dto import UserDTO, UpdatePreferencesCommand
from core.entities.user import User, QuotaAllocation, UserPreferences
from core.policies.quota_policy import QuotaPolicy
from core.exceptions.domain_exceptions import EntityNotFoundError


class UserManagementService:
    """
    سرویس مدیریت کاربران، سهمیه روزانه و ترجیحات سیستمی.
    """

    def __init__(self, uow_factory: IUnitOfWorkFactory):
        self.uow_factory = uow_factory

    def get_user_by_id(self, user_id: int) -> UserDTO:
        with self.uow_factory.create() as uow:
            user = uow.users.get_by_id(user_id)
            if not user:
                raise EntityNotFoundError("User", user_id)

            if QuotaPolicy.should_reset_quota(user.quota.last_active_date):
                uow.users.reset_daily_quota(user.id)
                user.quota.daily_pages_used = 0
                uow.commit()

            return self._to_dto(user)

    def get_or_create_telegram_user(self, telegram_id: int, username: Optional[str] = None) -> UserDTO:
        with self.uow_factory.create() as uow:
            user = uow.users.get_by_telegram_id(telegram_id)
            if not user:
                user = User(
                    id=None,
                    telegram_id=telegram_id,
                    username=username,
                    is_admin=False,
                    quota=QuotaAllocation(daily_limit=50, daily_pages_used=0, last_active_date=date.today()),
                    preferences=UserPreferences(),
                )
                user = uow.users.save(user)
                uow.commit()
            else:
                if QuotaPolicy.should_reset_quota(user.quota.last_active_date):
                    uow.users.reset_daily_quota(user.id)
                    user.quota.daily_pages_used = 0
                    uow.commit()

            return self._to_dto(user)

    def update_preferences(self, cmd: UpdatePreferencesCommand) -> UserDTO:
        with self.uow_factory.create() as uow:
            user = uow.users.get_by_id(cmd.user_id)
            if not user:
                raise EntityNotFoundError("User", cmd.user_id)

            if cmd.use_public_fallback is not None:
                user.preferences.use_public_fallback = cmd.use_public_fallback
            if cmd.auto_retry is not None:
                user.preferences.auto_retry = cmd.auto_retry
            if cmd.auto_pipeline2 is not None:
                user.preferences.auto_pipeline2 = cmd.auto_pipeline2
            if cmd.default_prompt_id is not None:
                user.preferences.default_prompt_id = cmd.default_prompt_id
            if cmd.default_pipeline2_prompt_id is not None:
                user.preferences.default_pipeline2_prompt_id = cmd.default_pipeline2_prompt_id

            uow.users.save(user)
            uow.commit()
            return self._to_dto(user)

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
