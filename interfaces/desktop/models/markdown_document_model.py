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
    Signal,
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

    generationChanged = Signal(int)

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
    PrimaryOccurrenceIdRole = Qt.ItemDataRole.UserRole + 18
    ListItemSegmentsRole = Qt.ItemDataRole.UserRole + 19
    TableCellSegmentsRole = Qt.ItemDataRole.UserRole + 20
    QuoteChildrenRole = Qt.ItemDataRole.UserRole + 21
    SourceStartLineRole = Qt.ItemDataRole.UserRole + 22
    SourceEndLineRole = Qt.ItemDataRole.UserRole + 23

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._items: List[Dict[str, Any]] = []
        self._model_generation: int = 0
        self._document_dto: Optional[MarkdownDocumentDTO] = None
        self._region_to_node_index: Dict[str, int] = {}
        self._region_to_occurrences: Dict[str, List[Dict[str, Any]]] = {}
        self._occurrence_to_node_index: Dict[str, int] = {}
        self._region_to_page_number: Dict[str, int] = {}

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
            self.PrimaryOccurrenceIdRole: b"primaryOccurrenceId",
            self.ListItemSegmentsRole: b"listItemSegments",
            self.TableCellSegmentsRole: b"tableCellSegments",
            self.QuoteChildrenRole: b"quoteChildren",
            self.IsAssociatedRole: b"isAssociated",
            self.DisplayOrderRole: b"displayOrder",
            self.PageNumberRole: b"pageNumber",
            self.ImageUriRole: b"imageUri",
            self.AltTextRole: b"altText",
            self.SourceStartLineRole: b"sourceStartLine",
            self.SourceEndLineRole: b"sourceEndLine",
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
        elif role == self.PrimaryOccurrenceIdRole:
            return item["primaryOccurrenceId"]
        elif role == self.ListItemSegmentsRole:
            return item["listItemSegments"]
        elif role == self.TableCellSegmentsRole:
            return item["tableCellSegments"]
        elif role == self.QuoteChildrenRole:
            return item["quoteChildren"]
        elif role == self.SourceStartLineRole:
            return item.get("sourceStartLine")
        elif role == self.SourceEndLineRole:
            return item.get("sourceEndLine")
        return None

    def _node_dto_to_item(self, node: Any) -> Dict[str, Any]:
        """Converts a MarkdownNodeDTO into a dictionary for QML model roles."""
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
        primary_occurrence_id = ""
        is_associated = False
        display_order = 0
        page_number = 0
        image_uri = ""
        alt_text = ""

        if node.regions:
            primary = node.regions[0]
            primary_region_id = primary.region_id or ""
            primary_occurrence_id = primary.occurrence_id or ""
            is_associated = primary.is_associated
            display_order = primary.display_order or 0
            page_number = primary.page_number or 0
            image_uri = self._resolve_qml_uri(primary)
            alt_text = primary.alt_text

        list_item_seg_dicts = []
        for item_segs in node.list_item_segments:
            sub_dicts = [
                {
                    "segmentType": s.segment_type,
                    "textHtml": s.text_html,
                    "imageRef": self._ref_to_dict(s.image_ref) if s.image_ref else None,
                }
                for s in item_segs
            ]
            list_item_seg_dicts.append(sub_dicts)

        table_cell_seg_dicts = []
        for row in node.table_cell_segments:
            row_dicts = []
            for col in row:
                col_dicts = [
                    {
                        "segmentType": s.segment_type,
                        "textHtml": s.text_html,
                        "imageRef": self._ref_to_dict(s.image_ref) if s.image_ref else None,
                    }
                    for s in col
                ]
                row_dicts.append(col_dicts)
            table_cell_seg_dicts.append(row_dicts)

        quote_child_dicts = []
        for q_child in node.quote_children:
            q_segs = [
                {
                    "segmentType": s.segment_type,
                    "textHtml": s.text_html,
                    "imageRef": self._ref_to_dict(s.image_ref) if s.image_ref else None,
                }
                for s in q_child.segments
            ]
            quote_child_dicts.append({
                "childType": q_child.child_type,
                "content": q_child.content,
                "level": q_child.level,
                "segments": q_segs,
            })

        return {
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
            "listItemSegments": list_item_seg_dicts,
            "tableCellSegments": table_cell_seg_dicts,
            "quoteChildren": quote_child_dicts,
            "regions": reg_dicts,
            "primaryRegionId": primary_region_id,
            "primaryOccurrenceId": primary_occurrence_id,
            "isAssociated": is_associated,
            "displayOrder": display_order,
            "pageNumber": page_number,
            "imageUri": image_uri,
            "altText": alt_text,
            "sourceStartLine": node.source_start_line,
            "sourceEndLine": node.source_end_line,
        }

    def set_document(self, document_dto: Optional[MarkdownDocumentDTO]) -> None:
        """
        Atomically updates the model on the Qt GUI thread.
        Transforms domain DTOs to QVariant-safe dictionaries and builds O(1) indices.
        """
        self.beginResetModel()
        self._items.clear()
        self._region_to_node_index.clear()
        self._region_to_occurrences.clear()
        self._occurrence_to_node_index.clear()
        self._region_to_page_number.clear()

        if document_dto is not None:
            self._document_dto = document_dto
            for idx, node in enumerate(document_dto.nodes):
                item = self._node_dto_to_item(node)
                self._items.append(item)

                for r in node.regions:
                    if r.region_id:
                        if r.region_id not in self._region_to_node_index:
                            self._region_to_node_index[r.region_id] = idx
                        if r.page_number and r.region_id not in self._region_to_page_number:
                            self._region_to_page_number[r.region_id] = r.page_number

            for rid, occ_refs in document_dto.region_to_occurrences.items():
                self._region_to_occurrences[rid] = [
                    {"nodeIndex": o.node_index, "occurrenceId": o.occurrence_id}
                    for o in occ_refs
                ]
                for o in occ_refs:
                    self._occurrence_to_node_index[o.occurrence_id] = o.node_index
        else:
            self._document_dto = None

        self._model_generation += 1
        self.endResetModel()
        self.generationChanged.emit(self._model_generation)

    @Slot(object)
    def apply_transient_preview(self, document_dto: Optional[MarkdownDocumentDTO]) -> None:
        """
        Applies an ephemeral AST projection to the presentation model.
        Delegates to reconcile_document(document_dto) while guaranteeing
        zero canonical version mutation or version signal emission.
        """
        self.reconcile_document(document_dto)

    @Slot(object)
    def reconcile_document(self, document_dto: Optional[MarkdownDocumentDTO]) -> None:
        """
        Non-destructively reconciles the model with the canonical MarkdownDocumentDTO.
        Identifies newly-added canonical nodes using stable nodeId and inserts them
        via beginInsertRows() / endInsertRows() without resetting the model or losing scroll.
        Updates in-place roles for existing nodes via dataChanged().
        If unsupported structural changes (deletions, reordering) are detected,
        safely falls back to set_document().
        """
        if document_dto is None:
            self.set_document(None)
            return

        if not self._items:
            self.set_document(document_dto)
            return

        old_ids = [item["nodeId"] for item in self._items]
        new_nodes = document_dto.nodes
        new_ids = [node.node_id for node in new_nodes]

        # Verify old_ids is a strict subsequence of new_ids
        new_id_to_idx = {nid: i for i, nid in enumerate(new_ids)}
        prev_idx = -1
        is_subsequence = True
        for oid in old_ids:
            if oid not in new_id_to_idx:
                is_subsequence = False
                break
            n_idx = new_id_to_idx[oid]
            if n_idx <= prev_idx:
                is_subsequence = False
                break
            prev_idx = n_idx

        if not is_subsequence:
            # Fall back to safe reset if structural order or deletions were detected
            self.set_document(document_dto)
            return

        # Addition-only structural synchronization (with targeted in-place updates for existing nodes)
        old_ptr = 0
        for new_idx, new_node in enumerate(new_nodes):
            if old_ptr < len(self._items) and self._items[old_ptr]["nodeId"] == new_node.node_id:
                # Existing node: check for targeted updates (e.g. image URI or content update)
                new_item_dict = self._node_dto_to_item(new_node)
                old_item_dict = self._items[old_ptr]

                roles_changed = []
                if old_item_dict.get("imageUri") != new_item_dict.get("imageUri"):
                    roles_changed.append(self.ImageUriRole)
                if old_item_dict.get("segments") != new_item_dict.get("segments"):
                    roles_changed.append(self.SegmentsRole)
                if old_item_dict.get("regions") != new_item_dict.get("regions"):
                    roles_changed.append(self.RegionsRole)
                if old_item_dict.get("content") != new_item_dict.get("content"):
                    roles_changed.append(self.ContentRole)
                if old_item_dict.get("sourceStartLine") != new_item_dict.get("sourceStartLine"):
                    roles_changed.append(self.SourceStartLineRole)
                if old_item_dict.get("sourceEndLine") != new_item_dict.get("sourceEndLine"):
                    roles_changed.append(self.SourceEndLineRole)

                if roles_changed:
                    self._items[old_ptr] = new_item_dict
                    m_idx = self.index(old_ptr, 0)
                    self.dataChanged.emit(m_idx, m_idx, roles_changed)

                old_ptr += 1
            else:
                # New canonical node to insert at old_ptr
                new_item_dict = self._node_dto_to_item(new_node)
                self.beginInsertRows(QModelIndex(), old_ptr, old_ptr)
                self._items.insert(old_ptr, new_item_dict)
                self.endInsertRows()
                old_ptr += 1

        # Canonical lookup structures rebuild from document_dto
        self._document_dto = document_dto
        self._region_to_node_index.clear()
        self._region_to_occurrences.clear()
        self._occurrence_to_node_index.clear()
        self._region_to_page_number.clear()

        for idx, node in enumerate(document_dto.nodes):
            for r in node.regions:
                if r.region_id:
                    if r.region_id not in self._region_to_node_index:
                        self._region_to_node_index[r.region_id] = idx
                    if r.page_number and r.region_id not in self._region_to_page_number:
                        self._region_to_page_number[r.region_id] = r.page_number

        for rid, occ_refs in document_dto.region_to_occurrences.items():
            self._region_to_occurrences[rid] = [
                {"nodeIndex": o.node_index, "occurrenceId": o.occurrence_id}
                for o in occ_refs
            ]
            for o in occ_refs:
                self._occurrence_to_node_index[o.occurrence_id] = o.node_index

        self._model_generation += 1
        self.generationChanged.emit(self._model_generation)

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

    @Slot(str, result=str)
    def primaryOccurrenceOfRegion(self, region_id: str) -> str:
        """Returns the primary occurrence_id for the given region_id, or empty string."""
        if not region_id:
            return ""
        occs = self._region_to_occurrences.get(region_id, [])
        return occs[0]["occurrenceId"] if occs else ""

    @Slot(str, result=int)
    def indexOfOccurrence(self, occurrence_id: str) -> int:
        """Returns the node index for a specific occurrence_id in O(1), or -1 if not found."""
        if not occurrence_id:
            return -1
        return self._occurrence_to_node_index.get(occurrence_id, -1)

    @Slot(str, result=int)
    def pageNumberOfRegion(self, region_id: str) -> int:
        """Returns the 1-based page number for the given region_id in O(1), or 0 if not found."""
        if not region_id:
            return 0
        return self._region_to_page_number.get(region_id, 0)

    @Slot(int, result="QVariantMap")
    def getNode(self, index: int) -> Optional[Dict[str, Any]]:
        """Direct access to block node dictionary by index."""
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    @Slot(str, str, int)
    @Slot(str, str)
    def update_region_artifact(
        self, region_id: str, new_artifact_uri: str, new_version: int = 1
    ) -> None:
        """
        Updates the active artifact URI for all occurrences of region_id in-place.
        Emits targeted dataChanged signals for modified node rows without model reset.
        """
        if not region_id:
            return

        if new_artifact_uri:
            qml_uri = (
                new_artifact_uri
                if new_artifact_uri.startswith("file:")
                else QUrl.fromLocalFile(new_artifact_uri).toString()
            )
        else:
            qml_uri = ""

        affected_indices = set()
        for occ in self._region_to_occurrences.get(region_id, []):
            affected_indices.add(occ["nodeIndex"])
        if region_id in self._region_to_node_index:
            affected_indices.add(self._region_to_node_index[region_id])

        if not affected_indices:
            for idx, item in enumerate(self._items):
                if item.get("primaryRegionId") == region_id or any(
                    r.get("regionId") == region_id for r in item.get("regions", [])
                ):
                    affected_indices.add(idx)

        for node_idx in sorted(affected_indices):
            if node_idx < 0 or node_idx >= len(self._items):
                continue
            item = self._items[node_idx]
            modified = False

            if item.get("primaryRegionId") == region_id:
                item["imageUri"] = qml_uri
                modified = True

            new_regions = []
            for r in item.get("regions", []):
                if r.get("regionId") == region_id:
                    new_r = dict(r)
                    new_r["imageUri"] = qml_uri
                    new_r["imagePath"] = new_artifact_uri
                    new_regions.append(new_r)
                    modified = True
                else:
                    new_regions.append(r)
            if modified:
                item["regions"] = new_regions

            if item.get("segments"):
                new_segs = []
                for s in item["segments"]:
                    img_ref = s.get("imageRef")
                    if img_ref and img_ref.get("regionId") == region_id:
                        new_img_ref = dict(img_ref)
                        new_img_ref["imageUri"] = qml_uri
                        new_img_ref["imagePath"] = new_artifact_uri
                        new_s = dict(s)
                        new_s["imageRef"] = new_img_ref
                        new_segs.append(new_s)
                        modified = True
                    else:
                        new_segs.append(s)
                item["segments"] = new_segs

            if item.get("listItemSegments"):
                new_list_items = []
                for item_segs in item["listItemSegments"]:
                    new_sub_segs = []
                    for s in item_segs:
                        img_ref = s.get("imageRef")
                        if img_ref and img_ref.get("regionId") == region_id:
                            new_img_ref = dict(img_ref)
                            new_img_ref["imageUri"] = qml_uri
                            new_img_ref["imagePath"] = new_artifact_uri
                            new_s = dict(s)
                            new_s["imageRef"] = new_img_ref
                            new_sub_segs.append(new_s)
                            modified = True
                        else:
                            new_sub_segs.append(s)
                    new_list_items.append(new_sub_segs)
                item["listItemSegments"] = new_list_items

            if item.get("tableCellSegments"):
                new_table = []
                for row in item["tableCellSegments"]:
                    new_row = []
                    for col in row:
                        new_col = []
                        for s in col:
                            img_ref = s.get("imageRef")
                            if img_ref and img_ref.get("regionId") == region_id:
                                new_img_ref = dict(img_ref)
                                new_img_ref["imageUri"] = qml_uri
                                new_img_ref["imagePath"] = new_artifact_uri
                                new_s = dict(s)
                                new_s["imageRef"] = new_img_ref
                                new_col.append(new_s)
                                modified = True
                            else:
                                new_col.append(s)
                        new_row.append(new_col)
                    new_table.append(new_row)
                item["tableCellSegments"] = new_table

            if item.get("quoteChildren"):
                new_quote_children = []
                for q_child in item["quoteChildren"]:
                    new_q_segs = []
                    for s in q_child.get("segments", []):
                        img_ref = s.get("imageRef")
                        if img_ref and img_ref.get("regionId") == region_id:
                            new_img_ref = dict(img_ref)
                            new_img_ref["imageUri"] = qml_uri
                            new_img_ref["imagePath"] = new_artifact_uri
                            new_s = dict(s)
                            new_s["imageRef"] = new_img_ref
                            new_q_segs.append(new_s)
                            modified = True
                        else:
                            new_q_segs.append(s)
                    new_qc = dict(q_child)
                    new_qc["segments"] = new_q_segs
                    new_quote_children.append(new_qc)
                item["quoteChildren"] = new_quote_children

            if modified:
                model_idx = self.index(node_idx, 0)
                self.dataChanged.emit(
                    model_idx,
                    model_idx,
                    [
                        self.ImageUriRole,
                        self.SegmentsRole,
                        self.ListItemSegmentsRole,
                        self.TableCellSegmentsRole,
                        self.QuoteChildrenRole,
                        self.RegionsRole,
                    ],
                )

    @property
    def model_generation(self) -> int:
        return self._model_generation

    @Slot(result=int)
    def modelGeneration(self) -> int:
        return self._model_generation

    @Slot()
    def clear(self) -> None:
        """Clears the document model and increments generation."""
        self.set_document(None)

    @Slot(int, result=int)
    def nodeIndexAtLine(self, line: int) -> int:
        """
        Maps a 1-based source markdown line number to the corresponding 0-based
        presentation AST node index.
        Deterministic fallback rules:
        - If document is empty: returns -1.
        - If line <= 0 or before the first block: returns 0.
        - If line falls within a block [start, end]: returns that block's index.
        - If line falls in whitespace between blocks: returns the nearest preceding block index.
        - If line is beyond the last block: returns the last block index.
        """
        if not self._items:
            return -1
        if line <= 0:
            return 0

        best_idx = 0
        for idx, item in enumerate(self._items):
            start = item.get("sourceStartLine")
            end = item.get("sourceEndLine")
            if start is None or start <= 0:
                continue
            if line < start:
                # Line falls in whitespace before this block: return preceding block
                return max(0, idx - 1) if idx > 0 else 0
            if end is not None and start <= line <= end:
                return idx
            if start <= line:
                best_idx = idx

        return best_idx

    @Slot(int, result=int)
    def lineAtNodeIndex(self, node_index: int) -> int:
        """
        Returns the 1-based start line of the block at node_index.
        Falls back to line 1 if node_index is out of bounds or line info is missing.
        """
        if 0 <= node_index < len(self._items):
            start = self._items[node_index].get("sourceStartLine")
            if start is not None and start > 0:
                return start
        return 1
