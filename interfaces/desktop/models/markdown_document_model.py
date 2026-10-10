# ============================================================
#  interfaces/desktop/models/markdown_document_model.py
#  QAbstractListModel for Virtualized Markdown Document AST
# ============================================================

import html
import logging
import re
from typing import Any, Callable, Dict, List, Optional
import urllib.parse

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
from application.ports.math_renderer import MathRenderResult
from interfaces.desktop.providers.math_image_provider import (
    DEFAULT_MATH_THEME,
    MATH_THEME_COLORS,
    inject_svg_color,
    parse_dimension,
)

logger = logging.getLogger(__name__)

def _is_escaped(s: str, index: int) -> bool:
    """Returns True if the character at index in s is preceded by an odd number of backslashes."""
    count = 0
    i = index - 1
    while i >= 0 and s[i] == "\\":
        count += 1
        i -= 1
    return (count % 2) == 1


def normalize_math_tex(tex: Optional[str]) -> str:
    """
    Normalizes TeX math content by stripping supported outer delimiters ($$, $, \\[\\] and \\(\\)).
    Does not strip escaped dollar signs or malformed/unwrapped delimiters.
    """
    if not tex or not isinstance(tex, str):
        return ""
    s = tex.strip()
    if not s or s in ("$$", "$", r"\[\]", r"\(\)"):
        return ""

    # Check for $$...$$
    if s.startswith("$$") and s.endswith("$$") and len(s) >= 4:
        if not _is_escaped(s, len(s) - 2):
            return s[2:-2].strip()

    # Check for $...$
    if s.startswith("$") and not s.startswith("$$") and s.endswith("$") and not s.endswith("$$") and len(s) >= 2:
        if not _is_escaped(s, len(s) - 1):
            return s[1:-1].strip()

    # Check for \[...\]
    if s.startswith(r"\[") and s.endswith(r"\]") and len(s) >= 4:
        if not _is_escaped(s, len(s) - 2):
            return s[2:-2].strip()

    # Check for \(...\)
    if s.startswith(r"\(") and s.endswith(r"\)") and len(s) >= 4:
        if not _is_escaped(s, len(s) - 2):
            return s[2:-2].strip()

    return s


_FORMULA_IMG_PATTERN = re.compile(
    r'<img\b(?P<attrs>[^>]*?)(?<![\w-])src\s*=\s*(?:["\']image://math/(?:(?P<theme>[^/"\'\s<>]+)/)?(?P<hash>[^/"\'\s<>]+)["\']|image://math/(?:(?P<theme_u>[^/"\'\s<>]+)/)?(?P<hash_u>[^\s/>]+))(?P<trailing>[^>]*?)/?>',
    re.IGNORECASE,
)

_HTML_ATTR_PATTERN = re.compile(
    r'(?P<name>[a-zA-Z_:][a-zA-Z0-9._:-]*)(?:\s*=\s*(?P<val>"[^"]*"|\'[^\']*\'|[^\s/>]+))?',
    re.IGNORECASE,
)


def _filter_img_attributes(attrs_str: str) -> str:
    """
    Extracts and preserves non-dimension, non-alignment attributes from an <img> tag.
    Safely eliminates existing align, width, height, and src attributes (quoted or unquoted),
    while preserving unrelated attributes and quoted attribute values containing spaces.
    """
    if not attrs_str:
        return ""
    cleaned = re.sub(r'/\s*$', '', attrs_str).strip()
    if not cleaned:
        return ""
    kept: List[str] = []
    for m in _HTML_ATTR_PATTERN.finditer(cleaned):
        name = m.group("name")
        if name.lower() in ("align", "width", "height", "src"):
            continue
        val = m.group("val")
        if val is not None:
            kept.append(f"{name}={val}")
        else:
            kept.append(name)
    return " ".join(kept)


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
    MathTexRole = Qt.ItemDataRole.UserRole + 24
    MathHashRole = Qt.ItemDataRole.UserRole + 25
    SourceStartColRole = Qt.ItemDataRole.UserRole + 26
    SourceEndColRole = Qt.ItemDataRole.UserRole + 27
    MathHasErrorRole = Qt.ItemDataRole.UserRole + 28
    MathErrorCategoryRole = Qt.ItemDataRole.UserRole + 29
    MathErrorMessageRole = Qt.ItemDataRole.UserRole + 30

    def __init__(
        self,
        parent: Optional[QObject] = None,
        math_resolver: Optional[Callable[[str], Optional[MathRenderResult]]] = None,
        math_foreground: str = MATH_THEME_COLORS[DEFAULT_MATH_THEME],
    ):
        super().__init__(parent)
        self._items: List[Dict[str, Any]] = []
        self._model_generation: int = 0
        self._document_dto: Optional[MarkdownDocumentDTO] = None
        self._region_to_node_index: Dict[str, int] = {}
        self._region_to_occurrences: Dict[str, List[Dict[str, Any]]] = {}
        self._occurrence_to_node_index: Dict[str, int] = {}
        self._region_to_page_number: Dict[str, int] = {}
        self._math_resolver: Optional[Callable[[str], Optional[MathRenderResult]]] = math_resolver
        self._math_foreground: str = math_foreground
        self._doc_hash_to_tex: Dict[str, str] = {}

    @property
    def math_resolver(self) -> Optional[Callable[[str], Optional[MathRenderResult]]]:
        """Returns the active math presentation resolver."""
        return self._math_resolver

    @property
    def math_foreground(self) -> str:
        """Returns the active semantic foreground color for math SVG rendering."""
        return self._math_foreground

    def set_math_presentation_resolver(
        self, resolver: Optional[Callable[[str], Optional[MathRenderResult]]]
    ) -> None:
        """Injects presentation resolver for formula SVGs and reprojects active document."""
        self._math_resolver = resolver
        if self._document_dto is not None and self._items:
            self.reproject_math()

    set_math_resolver = set_math_presentation_resolver

    def set_math_foreground(self, color_hex: str) -> None:
        """Updates active semantic foreground color and reprojects formulas in-place."""
        self.reproject_math(color_hex)

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
            self.MathTexRole: b"mathTex",
            self.MathHashRole: b"mathHash",
            self.SourceStartColRole: b"sourceStartCol",
            self.SourceEndColRole: b"sourceEndCol",
            self.MathHasErrorRole: b"mathHasError",
            self.MathErrorCategoryRole: b"mathErrorCategory",
            self.MathErrorMessageRole: b"mathErrorMessage",
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
        elif role == self.MathTexRole:
            return item.get("mathTex", "")
        elif role == self.MathHashRole:
            return item.get("mathHash", "")
        elif role == self.SourceStartColRole:
            return item.get("sourceStartCol")
        elif role == self.SourceEndColRole:
            return item.get("sourceEndCol")
        elif role == self.MathHasErrorRole:
            return bool(item.get("mathHasError", False))
        elif role == self.MathErrorCategoryRole:
            return str(item.get("mathErrorCategory", "") or "")
        elif role == self.MathErrorMessageRole:
            return str(item.get("mathErrorMessage", "") or "")
        return None

    def _update_doc_hash_to_tex(self, document_dto: Optional[MarkdownDocumentDTO]) -> None:
        """Indexes all formula TeX strings by their hash across the document."""
        self._doc_hash_to_tex.clear()
        if document_dto is None:
            return
        for node in document_dto.nodes:
            if getattr(node, "math_hash", None) and getattr(node, "math_tex", None):
                self._doc_hash_to_tex[node.math_hash] = node.math_tex
            for s in getattr(node, "segments", ()) or ():
                if getattr(s, "math_hash", None) and getattr(s, "math_tex", None):
                    self._doc_hash_to_tex[s.math_hash] = s.math_tex
            for item_segs in getattr(node, "list_item_segments", ()) or ():
                for s in item_segs:
                    if getattr(s, "math_hash", None) and getattr(s, "math_tex", None):
                        self._doc_hash_to_tex[s.math_hash] = s.math_tex
            for row in getattr(node, "table_cell_segments", ()) or ():
                for col in row:
                    for s in col:
                        if getattr(s, "math_hash", None) and getattr(s, "math_tex", None):
                            self._doc_hash_to_tex[s.math_hash] = s.math_tex
            for q_child in getattr(node, "quote_children", ()) or ():
                for s in getattr(q_child, "segments", ()) or ():
                    if getattr(s, "math_hash", None) and getattr(s, "math_tex", None):
                        self._doc_hash_to_tex[s.math_hash] = s.math_tex

    def _project_html(self, html_text: str, seg_tex: Optional[str] = None) -> str:
        """
        Transforms formula-bearing HTML tags (<img src="image://math/{hash}"...>)
        into theme-aware, dimensioned SVG Data URIs.
        On cache misses or resolver errors, produces safe readable text fallbacks.
        Preserves unrelated HTML, prose, and ordinary images.
        """
        if not html_text or not isinstance(html_text, str):
            return html_text or ""
        if "image://math/" not in html_text.lower():
            return html_text

        def _replace_formula_img(match: re.Match) -> str:
            formula_hash = match.group("hash") or match.group("hash_u") or ""
            attrs = match.group("attrs") or ""
            trailing = match.group("trailing") or ""

            result = None
            if self._math_resolver is not None:
                try:
                    if hasattr(self._math_resolver, "resolve"):
                        result = self._math_resolver.resolve(formula_hash)
                    elif callable(self._math_resolver):
                        result = self._math_resolver(formula_hash)
                except Exception as e:
                    logger.warning("Unexpected math presentation resolver error for hash %s: %s", formula_hash, e, exc_info=True)
                    result = None

            if result is not None and getattr(result, "svg_xml", None) and result.svg_xml.strip():
                try:
                    themed_svg = inject_svg_color(result.svg_xml, self._math_foreground)
                    data_uri = "data:image/svg+xml;utf8," + urllib.parse.quote(themed_svg)
                    w_px = parse_dimension(result.width, fallback=30.0, base_unit=8.0)
                    h_px = parse_dimension(result.height, fallback=15.0, base_unit=8.0)
                    w_val = max(1, round(w_px))
                    h_val = max(1, round(h_px))

                    extra = _filter_img_attributes(f"{attrs} {trailing}")
                    if extra:
                        return f'<img src="{data_uri}" width="{w_val}" height="{h_val}" align="middle" {extra}/>'
                    return f'<img src="{data_uri}" width="{w_val}" height="{h_val}" align="middle"/>'
                except Exception as e:
                    logger.warning("Unexpected math presentation SVG encoding error for hash %s: %s", formula_hash, e, exc_info=True)

            # Cache miss or resolver failure: readable text fallback
            raw_tex = seg_tex or self._doc_hash_to_tex.get(formula_hash) or ""
            clean_tex = normalize_math_tex(raw_tex)
            if clean_tex:
                escaped_tex = html.escape(clean_tex, quote=True)
                return f'<span class="math-fallback">${escaped_tex}$</span>'
            return '<span class="math-fallback">[Math]</span>'

        return _FORMULA_IMG_PATTERN.sub(_replace_formula_img, html_text)

    def _node_dto_to_item(self, node: Any) -> Dict[str, Any]:
        """Converts a MarkdownNodeDTO into a dictionary for QML model roles."""
        def _convert_segment(s: Any) -> Dict[str, Any]:
            s_tex = getattr(s, "math_tex", "") or ""
            return {
                "segmentType": s.segment_type,
                "textHtml": self._project_html(s.text_html, seg_tex=s_tex),
                "imageRef": self._ref_to_dict(s.image_ref) if s.image_ref else None,
                "mathTex": s_tex,
                "mathHash": getattr(s, "math_hash", "") or "",
                "hasError": bool(getattr(s, "has_error", False)),
                "errorCategory": str(getattr(s, "error_category", "") or ""),
                "errorMessage": str(getattr(s, "error_message", "") or ""),
            }

        seg_dicts = [_convert_segment(s) for s in node.segments]

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
            sub_dicts = [_convert_segment(s) for s in item_segs]
            list_item_seg_dicts.append(sub_dicts)

        table_cell_seg_dicts = []
        for row in node.table_cell_segments:
            row_dicts = []
            for col in row:
                col_dicts = [_convert_segment(s) for s in col]
                row_dicts.append(col_dicts)
            table_cell_seg_dicts.append(row_dicts)

        quote_child_dicts = []
        for q_child in node.quote_children:
            q_segs = [_convert_segment(s) for s in q_child.segments]
            quote_child_dicts.append({
                "childType": q_child.child_type,
                "content": self._project_html(q_child.content),
                "level": q_child.level,
                "segments": q_segs,
            })

        return {
            "nodeId": node.node_id,
            "nodeType": node.node_type,
            "content": self._project_html(node.content),
            "level": node.level,
            "language": node.language,
            "isOrdered": node.is_ordered,
            "startIndex": node.start_index,
            "rawMarkdown": node.raw_markdown,
            "listItems": [self._project_html(item) for item in node.list_items],
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
            "sourceStartCol": getattr(node, "source_start_col", None),
            "sourceEndCol": getattr(node, "source_end_col", None),
            "mathTex": getattr(node, "math_tex", "") or "",
            "mathHash": getattr(node, "math_hash", "") or "",
            "mathHasError": bool(getattr(node, "has_error", False)),
            "mathErrorCategory": str(getattr(node, "error_category", "") or ""),
            "mathErrorMessage": str(getattr(node, "error_message", "") or ""),
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

        self._update_doc_hash_to_tex(document_dto)

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

        self._update_doc_hash_to_tex(document_dto)

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
                if old_item_dict.get("listItems") != new_item_dict.get("listItems"):
                    roles_changed.append(self.ListItemsRole)
                if old_item_dict.get("listItemSegments") != new_item_dict.get("listItemSegments"):
                    roles_changed.append(self.ListItemSegmentsRole)
                if old_item_dict.get("tableCellSegments") != new_item_dict.get("tableCellSegments"):
                    roles_changed.append(self.TableCellSegmentsRole)
                if old_item_dict.get("quoteChildren") != new_item_dict.get("quoteChildren"):
                    roles_changed.append(self.QuoteChildrenRole)
                if old_item_dict.get("sourceStartLine") != new_item_dict.get("sourceStartLine"):
                    roles_changed.append(self.SourceStartLineRole)
                if old_item_dict.get("sourceEndLine") != new_item_dict.get("sourceEndLine"):
                    roles_changed.append(self.SourceEndLineRole)
                if old_item_dict.get("mathHasError") != new_item_dict.get("mathHasError"):
                    roles_changed.append(self.MathHasErrorRole)
                if old_item_dict.get("mathErrorCategory") != new_item_dict.get("mathErrorCategory"):
                    roles_changed.append(self.MathErrorCategoryRole)
                if old_item_dict.get("mathErrorMessage") != new_item_dict.get("mathErrorMessage"):
                    roles_changed.append(self.MathErrorMessageRole)

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

    @Slot(str)
    @Slot()
    def reproject_math(self, new_foreground: Optional[str] = None) -> None:
        """
        Reprojects formula HTML from canonical data using active foreground color.
        Emits targeted dataChanged notifications only for affected rows and roles.
        Preserves model generation, list virtualization, node identity, and region indexes.
        """
        if new_foreground is not None and new_foreground != self._math_foreground:
            self._math_foreground = new_foreground

        if self._document_dto is None or not self._items:
            return

        for idx, canonical_node in enumerate(self._document_dto.nodes):
            if idx >= len(self._items):
                break

            old_item = self._items[idx]
            new_item = self._node_dto_to_item(canonical_node)

            roles_changed = []
            if old_item.get("content") != new_item.get("content"):
                old_item["content"] = new_item["content"]
                roles_changed.append(self.ContentRole)

            if self._merge_segments_text_html(old_item.get("segments", []), new_item.get("segments", [])):
                roles_changed.append(self.SegmentsRole)

            if old_item.get("listItems") != new_item.get("listItems"):
                old_item["listItems"] = new_item["listItems"]
                roles_changed.append(self.ListItemsRole)

            if self._merge_nested_segments_text_html(
                old_item.get("listItemSegments", []), new_item.get("listItemSegments", [])
            ):
                roles_changed.append(self.ListItemSegmentsRole)

            if self._merge_table_segments_text_html(
                old_item.get("tableCellSegments", []), new_item.get("tableCellSegments", [])
            ):
                roles_changed.append(self.TableCellSegmentsRole)

            if self._merge_quote_children_text_html(
                old_item.get("quoteChildren", []), new_item.get("quoteChildren", [])
            ):
                roles_changed.append(self.QuoteChildrenRole)

            if roles_changed:
                m_idx = self.index(idx, 0)
                self.dataChanged.emit(m_idx, m_idx, roles_changed)

    @staticmethod
    def _merge_segments_text_html(
        old_segs: List[Dict[str, Any]], new_segs: List[Dict[str, Any]]
    ) -> bool:
        """Updates projected textHtml while preserving runtime region artifact fields."""
        changed = False
        for old_s, new_s in zip(old_segs, new_segs):
            if old_s.get("textHtml") != new_s.get("textHtml"):
                old_s["textHtml"] = new_s.get("textHtml", "")
                changed = True
        return changed

    @staticmethod
    def _merge_nested_segments_text_html(
        old_nested: List[List[Dict[str, Any]]], new_nested: List[List[Dict[str, Any]]]
    ) -> bool:
        changed = False
        for old_row, new_row in zip(old_nested, new_nested):
            for old_s, new_s in zip(old_row, new_row):
                if old_s.get("textHtml") != new_s.get("textHtml"):
                    old_s["textHtml"] = new_s.get("textHtml", "")
                    changed = True
        return changed

    @staticmethod
    def _merge_table_segments_text_html(
        old_table: List[List[List[Dict[str, Any]]]], new_table: List[List[List[Dict[str, Any]]]]
    ) -> bool:
        changed = False
        for old_row, new_row in zip(old_table, new_table):
            for old_col, new_col in zip(old_row, new_row):
                for old_s, new_s in zip(old_col, new_col):
                    if old_s.get("textHtml") != new_s.get("textHtml"):
                        old_s["textHtml"] = new_s.get("textHtml", "")
                        changed = True
        return changed

    @staticmethod
    def _merge_quote_children_text_html(
        old_qc: List[Dict[str, Any]], new_qc: List[Dict[str, Any]]
    ) -> bool:
        changed = False
        for old_child, new_child in zip(old_qc, new_qc):
            if old_child.get("content") != new_child.get("content"):
                old_child["content"] = new_child.get("content", "")
                changed = True
            for old_s, new_s in zip(old_child.get("segments", []), new_child.get("segments", [])):
                if old_s.get("textHtml") != new_s.get("textHtml"):
                    old_s["textHtml"] = new_s.get("textHtml", "")
                    changed = True
        return changed

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

    @Slot(int, result=int)
    def columnAtNodeIndex(self, node_index: int) -> int:
        """
        Returns the 1-based start column of the block at node_index.
        Falls back to column 1 if node_index is out of bounds or column info is missing.
        """
        if 0 <= node_index < len(self._items):
            start_col = self._items[node_index].get("sourceStartCol")
            if start_col is not None and start_col > 0:
                return start_col
        return 1

    @Slot(int, int, result=int)
    def nodeIndexAtPosition(self, line: int, col: int = 1) -> int:
        """
        Maps a 1-based line and column to the best-matching AST node index.
        Uses exact interval matching [start_line:start_col, end_line:end_col] if available,
        falling back to line matching.
        """
        if not self._items:
            return -1
        if line <= 0:
            return 0

        # Exact line and column interval check
        for idx, item in enumerate(self._items):
            s_line = item.get("sourceStartLine")
            e_line = item.get("sourceEndLine")
            s_col = item.get("sourceStartCol")
            e_col = item.get("sourceEndCol")
            if s_line is None or s_line <= 0:
                continue

            if s_line == line:
                if e_line == line and s_col is not None and e_col is not None:
                    if s_col <= col <= e_col:
                        return idx
                elif e_line is not None and e_line > line:
                    if s_col is None or col >= s_col:
                        return idx
            elif e_line is not None and s_line < line < e_line:
                return idx
            elif e_line is not None and line == e_line:
                if e_col is None or col <= e_col:
                    return idx

        return self.nodeIndexAtLine(line)
