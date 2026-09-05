# ============================================================
#  interfaces/desktop/models/markdown_document_model.py
#  QAbstractListModel for Virtualized Markdown Document AST
# ============================================================

from typing import Any, Dict, List, Optional
from interfaces.desktop.qt_compat import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    Qt,
    QUrl,
    Slot,
)
from application.dto.markdown_dto import (
    MarkdownDocumentDTO,
    VisualRegionRefDTO,
)


class MarkdownDocumentModel(QAbstractListModel):
    """
    QAbstractListModel exposing parsed, structured Markdown AST blocks to QML.
    Supports high-performance virtualization (ListView with reuseItems: true),
    deterministic role access, and O(1) visual region lookup.
    """

    NodeIdRole = Qt.ItemDataRole.UserRole + 1
    NodeTypeRole = Qt.ItemDataRole.UserRole + 2
    ContentRole = Qt.ItemDataRole.UserRole + 3
    LevelRole = Qt.ItemDataRole.UserRole + 4
    LanguageRole = Qt.ItemDataRole.UserRole + 5
    IsOrderedRole = Qt.ItemDataRole.UserRole + 6
    StartIndexRole = Qt.ItemDataRole.UserRole + 7
    RawMarkdownRole = Qt.ItemDataRole.UserRole + 8
    ListItemsRole = Qt.ItemDataRole.UserRole + 9
    SegmentsRole = Qt.ItemDataRole.UserRole + 10
    RegionsRole = Qt.ItemDataRole.UserRole + 11
    PrimaryRegionIdRole = Qt.ItemDataRole.UserRole + 12
    IsAssociatedRole = Qt.ItemDataRole.UserRole + 13
    DisplayOrderRole = Qt.ItemDataRole.UserRole + 14
    PageNumberRole = Qt.ItemDataRole.UserRole + 15
    ImageUriRole = Qt.ItemDataRole.UserRole + 16
    AltTextRole = Qt.ItemDataRole.UserRole + 17

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._items: List[Dict[str, Any]] = []
        self._document_dto: Optional[MarkdownDocumentDTO] = None
        self._region_to_node_index: Dict[str, int] = {}
        self._region_to_occurrences: Dict[str, List[Dict[str, Any]]] = {}

    def roleNames(self) -> Dict[int, bytes]:
        return {
            self.NodeIdRole: b"nodeId",
            self.NodeTypeRole: b"nodeType",
            self.ContentRole: b"content",
            self.LevelRole: b"level",
            self.LanguageRole: b"language",
            self.IsOrderedRole: b"isOrdered",
            self.StartIndexRole: b"startIndex",
            self.RawMarkdownRole: b"rawMarkdown",
            self.ListItemsRole: b"listItems",
            self.SegmentsRole: b"segments",
            self.RegionsRole: b"regions",
            self.PrimaryRegionIdRole: b"primaryRegionId",
            self.IsAssociatedRole: b"isAssociated",
            self.DisplayOrderRole: b"displayOrder",
            self.PageNumberRole: b"pageNumber",
            self.ImageUriRole: b"imageUri",
            self.AltTextRole: b"altText",
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._items):
            return None

        item = self._items[index.row()]
        if role == self.NodeIdRole:
            return item["nodeId"]
        elif role == self.NodeTypeRole:
            return item["nodeType"]
        elif role == self.ContentRole:
            return item["content"]
        elif role == self.LevelRole:
            return item["level"]
        elif role == self.LanguageRole:
            return item["language"]
        elif role == self.IsOrderedRole:
            return item["isOrdered"]
        elif role == self.StartIndexRole:
            return item["startIndex"]
        elif role == self.RawMarkdownRole:
            return item["rawMarkdown"]
        elif role == self.ListItemsRole:
            return item["listItems"]
        elif role == self.SegmentsRole:
            return item["segments"]
        elif role == self.RegionsRole:
            return item["regions"]
        elif role == self.PrimaryRegionIdRole:
            return item["primaryRegionId"]
        elif role == self.IsAssociatedRole:
            return item["isAssociated"]
        elif role == self.DisplayOrderRole:
            return item["displayOrder"]
        elif role == self.PageNumberRole:
            return item["pageNumber"]
        elif role == self.ImageUriRole:
            return item["imageUri"]
        elif role == self.AltTextRole:
            return item["altText"]
        return None

    def set_document(self, document_dto: Optional[MarkdownDocumentDTO]) -> None:
        """
        Atomically updates the model on the Qt GUI thread.
        Transforms domain DTOs to QVariant-safe dictionaries and builds O(1) indices.
        """
        self.beginResetModel()
        self._items.clear()
        self._region_to_node_index.clear()
        self._region_to_occurrences.clear()

        if document_dto is not None:
            self._document_dto = document_dto
            for idx, node in enumerate(document_dto.nodes):
                seg_dicts = []
                for s in node.segments:
                    s_dict = {
                        "segmentType": s.segment_type,
                        "textHtml": s.text_html,
                        "imageRef": self._ref_to_dict(s.image_ref) if s.image_ref else None,
                    }
                    seg_dicts.append(s_dict)

                reg_dicts = [self._ref_to_dict(r) for r in node.regions]

                primary_region_id = ""
                is_associated = False
                display_order = 0
                page_number = 0
                image_uri = ""
                alt_text = ""

                if node.regions:
                    primary = node.regions[0]
                    primary_region_id = primary.region_id or ""
                    is_associated = primary.is_associated
                    display_order = primary.display_order or 0
                    page_number = primary.page_number or 0
                    image_uri = self._resolve_qml_uri(primary)
                    alt_text = primary.alt_text

                for r in node.regions:
                    if r.region_id and r.region_id not in self._region_to_node_index:
                        self._region_to_node_index[r.region_id] = idx

                item = {
                    "nodeId": node.node_id,
                    "nodeType": node.node_type,
                    "content": node.content,
                    "level": node.level,
                    "language": node.language,
                    "isOrdered": node.is_ordered,
                    "startIndex": node.start_index,
                    "rawMarkdown": node.raw_markdown,
                    "listItems": list(node.list_items),
                    "segments": seg_dicts,
                    "regions": reg_dicts,
                    "primaryRegionId": primary_region_id,
                    "isAssociated": is_associated,
                    "displayOrder": display_order,
                    "pageNumber": page_number,
                    "imageUri": image_uri,
                    "altText": alt_text,
                }
                self._items.append(item)

            for rid, occ_refs in document_dto.region_to_occurrences.items():
                self._region_to_occurrences[rid] = [
                    {"nodeIndex": o.node_index, "occurrenceId": o.occurrence_id}
                    for o in occ_refs
                ]
        else:
            self._document_dto = None

        self.endResetModel()

    def _resolve_qml_uri(self, ref: VisualRegionRefDTO) -> str:
        """Converts filesystem image_path to a QML-safe file:// URI."""
        if ref.image_path:
            return QUrl.fromLocalFile(ref.image_path).toString()
        if ref.source and (
            ref.source.startswith("http://")
            or ref.source.startswith("https://")
            or ref.source.startswith("file://")
        ):
            return ref.source
        return ""

    def _ref_to_dict(self, ref: VisualRegionRefDTO) -> Dict[str, Any]:
        """Converts VisualRegionRefDTO into a QML-accessible dictionary."""
        return {
            "occurrenceId": ref.occurrence_id,
            "source": ref.source,
            "imageUri": self._resolve_qml_uri(ref),
            "altText": ref.alt_text,
            "regionId": ref.region_id or "",
            "isAssociated": ref.is_associated,
            "displayOrder": ref.display_order or 0,
            "pageNumber": ref.page_number or 0,
        }

    @Slot(str, result=int)
    def indexOfRegion(self, region_id: str) -> int:
        """Returns the 0-based node index of the first block containing the given region_id, or -1."""
        if not region_id:
            return -1
        return self._region_to_node_index.get(region_id, -1)

    @Slot(str, result="QVariantList")
    def occurrencesOfRegion(self, region_id: str) -> List[Dict[str, Any]]:
        """Returns all occurrences of the visual region across the document."""
        if not region_id:
            return []
        return self._region_to_occurrences.get(region_id, [])

    @Slot(int, result="QVariantMap")
    def getNode(self, index: int) -> Optional[Dict[str, Any]]:
        """Direct access to block node dictionary by index."""
        if 0 <= index < len(self._items):
            return self._items[index]
        return None
