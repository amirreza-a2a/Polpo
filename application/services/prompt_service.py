# ============================================================
#  application/services/prompt_service.py
# ============================================================

from typing import List, Optional
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.dto.prompt_dto import PromptDTO, CreatePromptCommand, UpdatePromptCommand
from core.entities.prompt import Prompt, PromptType
from core.exceptions.domain_exceptions import EntityNotFoundError


class PromptService:
    """
    Application service managing extraction and rewriting prompts.
    """

    def __init__(self, uow_factory: IUnitOfWorkFactory):
        self.uow_factory = uow_factory

    def list_prompts(self, prompt_type: Optional[str] = None) -> List[PromptDTO]:
        pt = PromptType(prompt_type) if prompt_type else None
        with self.uow_factory.create() as uow:
            entities = uow.prompts.list_all(pt)
            return [
                PromptDTO(
                    id=p.id,
                    name=p.name,
                    text=p.text,
                    prompt_type=p.prompt_type.value,
                    is_default=p.is_default,
                )
                for p in entities
            ]

    def get_prompt_by_id(self, prompt_id: int) -> PromptDTO:
        with self.uow_factory.create() as uow:
            p = uow.prompts.get_by_id(prompt_id)
            if not p:
                raise EntityNotFoundError("Prompt", prompt_id)
            return PromptDTO(
                id=p.id,
                name=p.name,
                text=p.text,
                prompt_type=p.prompt_type.value,
                is_default=p.is_default,
            )

    def create_prompt(self, cmd: CreatePromptCommand) -> PromptDTO:
        pt = PromptType(cmd.prompt_type)
        with self.uow_factory.create() as uow:
            prompt = Prompt(
                id=None,
                name=cmd.name,
                text=cmd.text,
                prompt_type=pt,
                is_default=cmd.is_default,
            )
            saved = uow.prompts.save(prompt)
            if cmd.is_default:
                uow.prompts.set_default(saved.id, pt)
            uow.commit()
            return PromptDTO(
                id=saved.id,
                name=saved.name,
                text=saved.text,
                prompt_type=saved.prompt_type.value,
                is_default=saved.is_default,
            )

    def update_prompt(self, cmd: UpdatePromptCommand) -> PromptDTO:
        with self.uow_factory.create() as uow:
            prompt = uow.prompts.get_by_id(cmd.prompt_id)
            if not prompt:
                raise EntityNotFoundError("Prompt", cmd.prompt_id)

            if cmd.name is not None:
                prompt.name = cmd.name
            if cmd.text is not None:
                prompt.text = cmd.text
            if cmd.is_default is not None:
                prompt.is_default = cmd.is_default

            saved = uow.prompts.save(prompt)
            if cmd.is_default:
                uow.prompts.set_default(saved.id, saved.prompt_type)
            uow.commit()

            return PromptDTO(
                id=saved.id,
                name=saved.name,
                text=saved.text,
                prompt_type=saved.prompt_type.value,
                is_default=saved.is_default,
            )

    def delete_prompt(self, prompt_id: int) -> bool:
        with self.uow_factory.create() as uow:
            res = uow.prompts.delete(prompt_id)
            uow.commit()
            return res

    def set_default(self, prompt_id: int, prompt_type: str) -> None:
        pt = PromptType(prompt_type)
        with self.uow_factory.create() as uow:
            uow.prompts.set_default(prompt_id, pt)
            uow.commit()

    def toggle_prompt(self, prompt_id: int, is_active: bool) -> bool:
        with self.uow_factory.create() as uow:
            res = uow.prompts.toggle_active(prompt_id, is_active)
            uow.commit()
            return res

    def get_quick_convert_prompt(self) -> Optional[str]:
        with self.uow_factory.create() as uow:
            return uow.prompts.get_quick_convert_prompt()

    def set_quick_convert_prompt(self, text: str) -> None:
        with self.uow_factory.create() as uow:
            uow.prompts.set_quick_convert_prompt(text)
            uow.commit()
