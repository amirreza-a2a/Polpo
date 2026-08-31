# ============================================================
#  application/services/api_service.py
# ============================================================

from typing import List
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.dto.api_dto import ApiSlotDTO, RegisterApiCommand, DonateApiCommand
from core.exceptions.domain_exceptions import EntityNotFoundError


class ApiManagementService:
    """
    سرویس مدیریت کلیدهای API خصوصی کاربران و اهداهای عمومی.
    """

    def __init__(self, uow_factory: IUnitOfWorkFactory):
        self.uow_factory = uow_factory

    def list_user_apis(self, user_id: int, include_public: bool = True) -> List[ApiSlotDTO]:
        with self.uow_factory.create() as uow:
            slots = uow.apis.list_by_user(user_id, include_public=include_public)
            return [
                ApiSlotDTO(
                    id=s.id,
                    provider=s.provider,
                    label=s.label,
                    slot_type=s.slot_type,
                    selected_model=s.selected_model,
                    base_url=s.base_url,
                    supported_models=s.supported_models,
                )
                for s in slots
            ]

    def register_private_api(self, cmd: RegisterApiCommand) -> ApiSlotDTO:
        with self.uow_factory.create() as uow:
            slot = uow.apis.save_private(
                user_id=cmd.user_id,
                provider=cmd.provider,
                api_key=cmd.api_key,
                label=cmd.label,
                model=cmd.selected_model,
                base_url=cmd.base_url,
            )
            uow.commit()
            return ApiSlotDTO(
                id=slot.id,
                provider=slot.provider,
                label=slot.label,
                slot_type=slot.slot_type,
                selected_model=slot.selected_model,
                base_url=slot.base_url,
                supported_models=slot.supported_models,
            )

    def delete_private_api(self, api_id: int, user_id: int) -> bool:
        with self.uow_factory.create() as uow:
            res = uow.apis.delete_private(api_id, user_id)
            uow.commit()
            return res

    def donate_api(self, cmd: DonateApiCommand) -> int:
        with self.uow_factory.create() as uow:
            donation_id = uow.donations.save_donation(
                user_id=cmd.user_id,
                provider=cmd.provider,
                api_key=cmd.api_key,
                label=cmd.label,
                models=cmd.models,
            )
            uow.commit()
            return donation_id
