# ============================================================
#  interfaces/desktop/models/api_slot_model.py
#  QAbstractListModel for BYOK API Slots
# ============================================================

from typing import List, Dict, Any, Optional
from interfaces.desktop.qt_compat import QAbstractListModel, QModelIndex, Qt, Slot
from application.services.api_key_service import ApiKeyService


class ApiSlotModel(QAbstractListModel):
    """
    QAbstractListModel exposing registered BYOK API slots to QML.
    Guarantees that no raw secrets or API keys are ever stored or returned by the model.
    """

    IdRole = Qt.ItemDataRole.UserRole + 1
    ProviderRole = Qt.ItemDataRole.UserRole + 2
    LabelRole = Qt.ItemDataRole.UserRole + 3
    SlotTypeRole = Qt.ItemDataRole.UserRole + 4
    SelectedModelRole = Qt.ItemDataRole.UserRole + 5
    BaseUrlRole = Qt.ItemDataRole.UserRole + 6
    SupportedModelsRole = Qt.ItemDataRole.UserRole + 7

    def __init__(
        self,
        api_key_service: ApiKeyService,
        controller: Optional[Any] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.api_key_service = api_key_service
        self.controller = controller
        self._slots: List[Dict[str, Any]] = []

        if self.controller is not None and hasattr(self.controller, "slots_changed"):
            self.controller.slots_changed.connect(self.reload_slots)

        self.reload_slots()

    def roleNames(self) -> Dict[int, bytes]:
        return {
            self.IdRole: b"id",
            self.ProviderRole: b"provider",
            self.LabelRole: b"label",
            self.SlotTypeRole: b"slotType",
            self.SelectedModelRole: b"selectedModel",
            self.BaseUrlRole: b"baseUrl",
            self.SupportedModelsRole: b"supportedModels",
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._slots)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._slots):
            return None

        slot = self._slots[index.row()]
        if role == self.IdRole:
            return slot["id"]
        elif role == self.ProviderRole:
            return slot["provider"]
        elif role == self.LabelRole:
            return slot["label"]
        elif role == self.SlotTypeRole:
            return slot["slot_type"]
        elif role == self.SelectedModelRole:
            return slot["selected_model"]
        elif role == self.BaseUrlRole:
            return slot["base_url"]
        elif role == self.SupportedModelsRole:
            return slot["supported_models"]

        return None

    @Slot()
    def reload_slots(self) -> None:
        """Reloads all API slots from the service without secret exposure."""
        self.beginResetModel()
        try:
            dtos = self.api_key_service.list_slots()
            self._slots = [
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
        except Exception:
            self._slots = []
        self.endResetModel()
