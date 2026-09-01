# ============================================================
#  interfaces/desktop/models/prompt_list_model.py
#  QAbstractListModel for System Prompts
# ============================================================

from typing import List, Dict, Any, Optional
from interfaces.desktop.qt_compat import QAbstractListModel, QModelIndex, Qt, Slot
from application.services.prompt_service import PromptService


class PromptListModel(QAbstractListModel):
    """
    QAbstractListModel exposing prompt templates to QML.
    """

    IdRole = Qt.ItemDataRole.UserRole + 1
    NameRole = Qt.ItemDataRole.UserRole + 2
    TextRole = Qt.ItemDataRole.UserRole + 3
    PromptTypeRole = Qt.ItemDataRole.UserRole + 4
    IsDefaultRole = Qt.ItemDataRole.UserRole + 5

    def __init__(
        self,
        prompt_service: PromptService,
        controller: Optional[Any] = None,
        prompt_type: Optional[str] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.prompt_service = prompt_service
        self.controller = controller
        self.prompt_type = prompt_type
        self._prompts: List[Dict[str, Any]] = []

        if self.controller is not None and hasattr(self.controller, "prompts_changed"):
            self.controller.prompts_changed.connect(self.reload_prompts)

        self.reload_prompts()

    def roleNames(self) -> Dict[int, bytes]:
        return {
            self.IdRole: b"id",
            self.NameRole: b"name",
            self.TextRole: b"text",
            self.PromptTypeRole: b"promptType",
            self.IsDefaultRole: b"isDefault",
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._prompts)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._prompts):
            return None

        p = self._prompts[index.row()]
        if role == self.IdRole:
            return p["id"]
        elif role == self.NameRole:
            return p["name"]
        elif role == self.TextRole:
            return p["text"]
        elif role == self.PromptTypeRole:
            return p["prompt_type"]
        elif role == self.IsDefaultRole:
            return p["is_default"]

        return None

    @Slot(str)
    @Slot()
    def reload_prompts(self, prompt_type: Optional[str] = None) -> None:
        """Reloads prompts from the service."""
        if prompt_type is not None:
            self.prompt_type = prompt_type if prompt_type.strip() else None

        self.beginResetModel()
        try:
            dtos = self.prompt_service.list_prompts(self.prompt_type)
            self._prompts = [
                {
                    "id": d.id,
                    "name": d.name,
                    "text": d.text,
                    "prompt_type": d.prompt_type,
                    "is_default": d.is_default,
                }
                for d in dtos
            ]
        except Exception:
            self._prompts = []
        self.endResetModel()
