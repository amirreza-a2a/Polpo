# ============================================================
#  interfaces/desktop/controllers/prompt_controller.py
#  Desktop Presentation Controller for System Prompt Templates
# ============================================================

from typing import Optional, List, Dict, Any
from interfaces.desktop.qt_compat import QObject, Slot, Signal
from application.dto.prompt_dto import CreatePromptCommand, UpdatePromptCommand
from application.services.prompt_service import PromptService
from application.sanitizer import sanitize_error_message


class PromptController(QObject):
    """
    Presentation controller for system prompts (Pipeline 1 extraction, Pipeline 2 rewrite, Quick Convert).
    """

    prompts_changed = Signal()
    error_occurred = Signal(str)

    def __init__(self, prompt_service: PromptService, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.prompt_service = prompt_service

    @Slot(str, result="QVariantList")
    @Slot(result="QVariantList")
    def list_prompts(self, prompt_type: str = "") -> List[Dict[str, Any]]:
        """Returns all prompt templates, optionally filtered by prompt_type."""
        try:
            pt = prompt_type.strip() if prompt_type and prompt_type.strip() else None
            dtos = self.prompt_service.list_prompts(pt)
            return [
                {
                    "id": d.id,
                    "name": d.name,
                    "text": d.text,
                    "prompt_type": d.prompt_type,
                    "is_default": d.is_default,
                }
                for d in dtos
            ]
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return []

    @Slot(str, str, str, bool, result=int)
    @Slot(str, str, str, result=int)
    def create_prompt(
        self,
        name: str,
        text: str,
        prompt_type: str,
        is_default: bool = False,
    ) -> int:
        """Creates a new prompt template."""
        try:
            cmd = CreatePromptCommand(
                name=name.strip(),
                text=text.strip(),
                prompt_type=prompt_type.strip(),
                is_default=is_default,
            )
            dto = self.prompt_service.create_prompt(cmd)
            self.prompts_changed.emit()
            return dto.id
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return 0

    @Slot(int, str, str, bool, result=bool)
    @Slot(int, str, str, result=bool)
    def update_prompt(
        self,
        prompt_id: int,
        name: str = "",
        text: str = "",
        is_default: bool = False,
    ) -> bool:
        """Updates an existing prompt template."""
        try:
            cmd = UpdatePromptCommand(
                prompt_id=prompt_id,
                name=name.strip() if name and name.strip() else None,
                text=text.strip() if text and text.strip() else None,
                is_default=is_default,
            )
            dto = self.prompt_service.update_prompt(cmd)
            self.prompts_changed.emit()
            return dto is not None
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, result=bool)
    def delete_prompt(self, prompt_id: int) -> bool:
        """Deletes a custom prompt template."""
        try:
            deleted = self.prompt_service.delete_prompt(prompt_id)
            if deleted:
                self.prompts_changed.emit()
            return deleted
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, str, result=bool)
    def set_default(self, prompt_id: int, prompt_type: str) -> bool:
        """Sets a prompt template as the default for its prompt_type."""
        try:
            self.prompt_service.set_default(prompt_id, prompt_type.strip())
            self.prompts_changed.emit()
            return True
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, bool, result=bool)
    def toggle_active(self, prompt_id: int, is_active: bool) -> bool:
        """Toggles active state of a prompt template."""
        try:
            res = self.prompt_service.toggle_prompt(prompt_id, is_active)
            if res:
                self.prompts_changed.emit()
            return res
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False
