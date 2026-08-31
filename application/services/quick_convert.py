# ============================================================
#  application/services/quick_convert.py
# ============================================================

from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.ai_executor import IAIExecutionService
from application.dto.quick_convert_dto import QuickConvertCommand, QuickConvertResultDTO
from core.entities.prompt import PromptType
from core.policies.quota_policy import QuotaPolicy
from core.ai.exceptions import AIChainExhaustedError
from core.exceptions.domain_exceptions import (
    EntityNotFoundError,
    QuotaExceededError,
)


class QuickConvertService:
    """
    سرویس تبدیل سریع یک تصویر منفرد به مارک‌داون.
    کلیه وظایف Fallback و Rate Limiting را به درگاه IAIExecutionService تفویض می‌کند.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        ai_executor: IAIExecutionService,
    ):
        self.uow_factory = uow_factory
        self.ai_executor = ai_executor

    def convert_image(self, cmd: QuickConvertCommand) -> QuickConvertResultDTO:
        with self.uow_factory.create() as uow:
            user = uow.users.get_by_id(cmd.user_id)
            if not user:
                raise EntityNotFoundError("User", cmd.user_id)

            if QuotaPolicy.should_reset_quota(user.quota.last_active_date):
                uow.users.reset_daily_quota(user.id)
                user.quota.daily_pages_used = 0

            if not QuotaPolicy.can_consume(user.quota, 1):
                raise QuotaExceededError("Daily page limit reached.")

            prompt_text = cmd.prompt_text
            if not prompt_text:
                prompt_entity = uow.prompts.get_default(PromptType.QUICK_CONVERT)
                if prompt_entity:
                    prompt_text = prompt_entity.text
                else:
                    prompt_text = "Transcribe this image into clean GitHub-flavored markdown."

            chain = uow.apis.list_by_user(user.id, include_public=user.preferences.use_public_fallback)
            if not chain:
                raise AIChainExhaustedError("No API slots available.")

        # تفویض اجرای استنتاج و مدیریت Fallback به درگاه مجری
        result, used_slot = self.ai_executor.execute_vision_with_fallback(
            chain=chain,
            image_bytes=cmd.image_bytes,
            prompt=prompt_text,
            at_page=1,
            mime_type=cmd.mime_type,
        )

        if result is None:
            raise AIChainExhaustedError("All available APIs failed to convert the image.")

        # ثبت کسر سهمیه پس از موفقیت
        with self.uow_factory.create() as uow:
            uow.users.increment_daily_pages(cmd.user_id, 1)
            if used_slot:
                uow.apis.report_pages_used(used_slot.id, used_slot.slot_type, 1)
            uow.commit()

        return QuickConvertResultDTO(
            markdown_content=result,
            used_api_label=used_slot.label if used_slot else None,
            pages_consumed=1,
        )
