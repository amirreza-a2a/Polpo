# ============================================================
#  application/services/quick_convert.py
# ============================================================

from application.ports.unit_of_work import IUnitOfWorkFactory
from application.ports.ai_executor import IAIExecutionService
from application.dto.quick_convert_dto import QuickConvertCommand, QuickConvertResultDTO
from core.entities.prompt import PromptType
from core.ai.exceptions import AIChainExhaustedError


class QuickConvertService:
    """
    Application service for single-image quick OCR conversion to markdown.
    Delegates vision inference and fallback chain execution to IAIExecutionService.
    Desktop execution operates strictly without user quota requirements.
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
            prompt_text = cmd.prompt_text
            if not prompt_text:
                if hasattr(uow.prompts, "get_quick_convert_prompt"):
                    custom_prompt = uow.prompts.get_quick_convert_prompt()
                    if custom_prompt:
                        prompt_text = custom_prompt

                if not prompt_text:
                    prompt_entity = uow.prompts.get_default(PromptType.QUICK_CONVERT)
                    if prompt_entity:
                        prompt_text = prompt_entity.text
                    else:
                        prompt_text = "Transcribe this image into clean GitHub-flavored markdown."

            if hasattr(uow.apis, "list_all"):
                chain = uow.apis.list_all()
            elif hasattr(uow.apis, "list_by_user"):
                chain = uow.apis.list_by_user(getattr(cmd, "user_id", 1), include_public=True)
            elif hasattr(uow.apis, "list_public"):
                chain = uow.apis.list_public()
            else:
                chain = []

            if not chain:
                raise AIChainExhaustedError("No API slots available for quick conversion.")

        # Delegate execution outside database transaction
        result, used_slot = self.ai_executor.execute_vision_with_fallback(
            chain=chain,
            image_bytes=cmd.image_bytes,
            prompt=prompt_text,
            at_page=1,
            mime_type=cmd.mime_type,
        )

        if result is None:
            raise AIChainExhaustedError("All available APIs failed to convert the image.")

        # Report API slot usage if supported
        if used_slot:
            with self.uow_factory.create() as uow:
                if hasattr(uow.apis, "report_pages_used"):
                    uow.apis.report_pages_used(used_slot.id, used_slot.slot_type, 1)
                uow.commit()

        return QuickConvertResultDTO(
            markdown_content=result,
            used_api_label=used_slot.label if used_slot else None,
            pages_consumed=1,
        )
