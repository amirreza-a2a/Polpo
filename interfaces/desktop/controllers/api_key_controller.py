# ============================================================
#  interfaces/desktop/controllers/api_key_controller.py
#  Desktop Presentation Controller for BYOK API Key Operations
# ============================================================

from typing import Optional, List, Dict, Any
from interfaces.desktop.qt_compat import QObject, Slot, Signal
from application.dto.api_dto import RegisterKeyCommand, UpdateKeyCommand
from application.services.api_key_service import ApiKeyService
from application.sanitizer import sanitize_error_message


class ApiKeyController(QObject):
    """
    Presentation controller for BYOK API key configuration.
    Coordinates key registration, updates, connectivity testing, and slot deletion.
    Guarantees zero raw credential storage or exposure in Qt properties, signals, or return values.
    """

    slots_changed = Signal()
    error_occurred = Signal(str)

    def __init__(self, api_key_service: ApiKeyService, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.api_key_service = api_key_service

    @Slot(str, str, str, str, str, result=bool)
    @Slot(str, str, str, str, result=bool)
    @Slot(str, str, str, result=bool)
    def register_key(
        self,
        provider: str,
        label: str,
        api_key: str,
        model: str = "",
        base_url: str = "",
    ) -> bool:
        """
        Registers a new BYOK slot.
        The secret api_key string is passed transiently to ApiKeyService and never stored in the controller.
        """
        try:
            cmd = RegisterKeyCommand(
                provider=provider.strip(),
                label=label.strip(),
                api_key=api_key.strip(),
                selected_model=model.strip() if model and model.strip() else None,
                base_url=base_url.strip() if base_url and base_url.strip() else None,
            )
            dto = self.api_key_service.register_key(cmd)
            self.slots_changed.emit()
            return dto is not None
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, str, str, str, str, result=bool)
    @Slot(int, str, str, str, result=bool)
    @Slot(int, str, str, result=bool)
    @Slot(int, str, result=bool)
    def update_key(
        self,
        slot_id: int,
        label: str = "",
        api_key: str = "",
        model: str = "",
        base_url: str = "",
    ) -> bool:
        """Updates slot metadata and optionally replaces the secret credential."""
        try:
            cmd = UpdateKeyCommand(
                slot_id=slot_id,
                label=label.strip() if label and label.strip() else None,
                api_key=api_key.strip() if api_key and api_key.strip() else None,
                selected_model=model.strip() if model and model.strip() else None,
                base_url=base_url.strip() if base_url and base_url.strip() else None,
            )
            dto = self.api_key_service.update_key(cmd)
            self.slots_changed.emit()
            return dto is not None
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, result=bool)
    def delete_key(self, slot_id: int) -> bool:
        """Deletes slot metadata and securely purges the stored secret."""
        try:
            deleted = self.api_key_service.delete_key(slot_id)
            if deleted:
                self.slots_changed.emit()
            return deleted
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, result=bool)
    def test_key(self, slot_id: int) -> bool:
        """Verifies that the slot credential can be resolved successfully."""
        try:
            return self.api_key_service.test_key(slot_id)
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(result="QVariantList")
    def list_slots(self) -> List[Dict[str, Any]]:
        """
        Returns all registered API slots as safe dictionaries containing NO raw secrets.
        """
        try:
            dtos = self.api_key_service.list_slots()
            return [
                {
                    "id": d.id,
                    "provider": d.provider,
                    "label": d.label,
                    "slot_type": d.slot_type,
                    "selected_model": d.selected_model or "",
                    "base_url": d.base_url or "",
                    "supported_models": d.supported_models or [],
                }
                for d in dtos
            ]
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return []
