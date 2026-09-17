# ============================================================
#  interfaces/desktop/controllers/markdown_editor_controller.py
#  Phase 10F.1 — Native Markdown Editor Presentation Controller
# ============================================================

import re
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from application.services.markdown_editor_service import MarkdownEditorService
from core.exceptions.domain_exceptions import StaleDocumentVersionError
from interfaces.desktop.qt_compat import (
    QObject,
    Property,
    Signal,
    Slot,
    QTextCursor,
    QTextDocument,
)
from interfaces.desktop.syntax.markdown_syntax_highlighter import MarkdownSyntaxHighlighter


class MarkdownEditorController(QObject):
    """
    Presentation controller for native Markdown editing.
    Coordinates source loading, explicit user edits, dirty-state tracking,
    optimistic concurrency-controlled saving, and conflict detection.

    Guarantees:
      - Asynchronous file I/O on background workers keeps the Qt GUI thread responsive.
      - Generation token tracking discards stale async load/save responses.
      - Conflict state is flagged only when dirty edits collide with newer canonical versions.
      - Discard cleanly reverts buffer to last saved state.
      - Clean architecture boundary: communicates with application services only.
    """

    sourceTextChanged = Signal()
    dirtyChanged = Signal()
    loadingChanged = Signal()
    savingChanged = Signal()
    errorChanged = Signal()
    activeJobChanged = Signal()
    activeVersionChanged = Signal()
    conflictChanged = Signal()
    searchStateChanged = Signal()
    searchVisibilityChanged = Signal()
    cursorMetricsChanged = Signal()
    documentMetricsChanged = Signal()

    saved = Signal(int)             # (new_version)
    discarded = Signal()
    conflictDetected = Signal(str)  # (conflict_message)
    matchSelected = Signal(int, int) # (start_utf16, end_utf16)

    _internalLoaded = Signal(int, str, int)          # (req_id, text, version)
    _internalLoadError = Signal(int, str)            # (req_id, error_message)
    _internalSaved = Signal(int, int, str)           # (req_id, new_version, saved_text)
    _internalSaveError = Signal(int, str, bool)      # (req_id, error_message, is_conflict)

    def __init__(
        self,
        editor_service: MarkdownEditorService,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.editor_service = editor_service

        self._source_text: str = ""
        self._saved_source_text: str = ""
        self._is_dirty: bool = False
        self._is_loading: bool = False
        self._is_saving: bool = False
        self._error_message: str = ""
        self._active_job_id: int = 0
        self._active_version: int = 0
        self._has_conflict: bool = False
        self._conflict_message: str = ""

        self._text_document: Optional[QTextDocument] = None
        self._highlighter: Optional[MarkdownSyntaxHighlighter] = None
        self._headless_doc: Optional[QTextDocument] = None

        self._search_query: str = ""
        self._replace_query: str = ""
        self._search_case_sensitive: bool = False
        self._search_whole_word: bool = False
        self._search_match_index: int = 0
        self._search_total_matches: int = 0
        self._is_search_open: bool = False
        self._is_replace_open: bool = False
        self._matches: List[Tuple[int, int]] = []

        self._cursor_line: int = 1
        self._cursor_column: int = 1
        self._character_count: int = 0
        self._word_count: int = 0

        self._request_id: int = 0
        self._is_shutdown: bool = False
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="MarkdownEditorWorker")

        self._internalLoaded.connect(self._on_internal_loaded)
        self._internalLoadError.connect(self._on_internal_load_error)
        self._internalSaved.connect(self._on_internal_saved)
        self._internalSaveError.connect(self._on_internal_save_error)

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    def source_text(self) -> str:
        return self._source_text

    @Slot(str)
    def setSourceText(self, text: str) -> None:
        """QML-invokable slot to update editor buffer text."""
        self.set_source_text(text)

    @Slot(str)
    def set_source_text(self, text: str) -> None:
        """Updates buffer text and evaluates dirty state against last saved snapshot."""
        if self._source_text != text:
            self._source_text = text
            if self._headless_doc is not None and self._headless_doc.toPlainText() != text:
                self._headless_doc.setPlainText(text)
            self.sourceTextChanged.emit()
            new_dirty = (self._source_text != self._saved_source_text)
            if self._is_dirty != new_dirty:
                self._is_dirty = new_dirty
                self.dirtyChanged.emit()
            self._character_count = len(self._source_text)
            self._word_count = len(re.findall(r"\S+", self._source_text))
            self.documentMetricsChanged.emit()
            if self._search_query:
                self._recompute_matches()

    sourceText = Property(str, source_text, set_source_text, notify=sourceTextChanged)

    def is_dirty(self) -> bool:
        return self._is_dirty

    isDirty = Property(bool, is_dirty, notify=dirtyChanged)

    def is_loading(self) -> bool:
        return self._is_loading

    isLoading = Property(bool, is_loading, notify=loadingChanged)

    def is_saving(self) -> bool:
        return self._is_saving

    isSaving = Property(bool, is_saving, notify=savingChanged)

    def error_message(self) -> str:
        return self._error_message

    errorMessage = Property(str, error_message, notify=errorChanged)

    def active_job_id(self) -> int:
        return self._active_job_id

    activeJobId = Property(int, active_job_id, notify=activeJobChanged)

    def active_version(self) -> int:
        return self._active_version

    activeVersion = Property(int, active_version, notify=activeVersionChanged)

    def has_conflict(self) -> bool:
        return self._has_conflict

    hasConflict = Property(bool, has_conflict, notify=conflictChanged)

    def conflict_message(self) -> str:
        return self._conflict_message

    conflictMessage = Property(str, conflict_message, notify=conflictChanged)

    def search_query(self) -> str:
        return self._search_query

    searchQuery = Property(str, search_query, notify=searchStateChanged)

    def replace_query(self) -> str:
        return self._replace_query

    replaceQuery = Property(str, replace_query, notify=searchStateChanged)

    def search_case_sensitive(self) -> bool:
        return self._search_case_sensitive

    searchCaseSensitive = Property(bool, search_case_sensitive, notify=searchStateChanged)

    def search_whole_word(self) -> bool:
        return self._search_whole_word

    searchWholeWord = Property(bool, search_whole_word, notify=searchStateChanged)

    def search_match_index(self) -> int:
        return self._search_match_index

    searchMatchIndex = Property(int, search_match_index, notify=searchStateChanged)

    def search_total_matches(self) -> int:
        return self._search_total_matches

    searchTotalMatches = Property(int, search_total_matches, notify=searchStateChanged)

    def is_search_open(self) -> bool:
        return self._is_search_open

    isSearchOpen = Property(bool, is_search_open, notify=searchVisibilityChanged)

    def is_replace_open(self) -> bool:
        return self._is_replace_open

    isReplaceOpen = Property(bool, is_replace_open, notify=searchVisibilityChanged)

    def cursor_line(self) -> int:
        return self._cursor_line

    cursorLine = Property(int, cursor_line, notify=cursorMetricsChanged)

    def cursor_column(self) -> int:
        return self._cursor_column

    cursorColumn = Property(int, cursor_column, notify=cursorMetricsChanged)

    def character_count(self) -> int:
        return self._character_count

    characterCount = Property(int, character_count, notify=documentMetricsChanged)

    def word_count(self) -> int:
        return self._word_count

    wordCount = Property(int, word_count, notify=documentMetricsChanged)

    # -----------------------------------------------------------------------
    # Operations
    # -----------------------------------------------------------------------

    @Slot(int)
    def loadSource(self, job_id: int) -> None:
        """Asynchronously loads raw Markdown source text for job_id."""
        self._request_id += 1
        req_id = self._request_id

        self._active_job_id = job_id
        self.activeJobChanged.emit()

        self._is_loading = True
        self.loadingChanged.emit()
        self._error_message = ""
        self.errorChanged.emit()
        self._has_conflict = False
        self._conflict_message = ""
        self.conflictChanged.emit()

        def _task():
            try:
                text, ver = self.editor_service.load_source_text(job_id)
                self._internalLoaded.emit(req_id, text, ver)
            except Exception as e:
                self._internalLoadError.emit(req_id, str(e))

        self._executor.submit(_task)

    def load_source_sync(self, job_id: int) -> None:
        """Synchronous source loader for testing and deterministic inspection."""
        self._request_id += 1
        req_id = self._request_id
        self._active_job_id = job_id
        self.activeJobChanged.emit()
        self._has_conflict = False
        self._conflict_message = ""
        self.conflictChanged.emit()

        try:
            text, ver = self.editor_service.load_source_text(job_id)
            self._on_internal_loaded(req_id, text, ver)
        except Exception as e:
            self._on_internal_load_error(req_id, str(e))

    @Slot()
    def save(self) -> None:
        """Asynchronously persists source text to a new immutable canonical document."""
        if not self._is_dirty or self._is_saving:
            return

        if self._has_conflict:
            self._error_message = "Cannot save: document has conflicting external changes. Please reload."
            self.errorChanged.emit()
            return

        self._request_id += 1
        req_id = self._request_id

        self._is_saving = True
        self.savingChanged.emit()
        self._error_message = ""
        self.errorChanged.emit()

        job_id = self._active_job_id
        raw_text = self._source_text
        base_ver = self._active_version

        def _task():
            try:
                new_ver = self.editor_service.commit_source_text(
                    job_id=job_id,
                    raw_text=raw_text,
                    base_version=base_ver,
                )
                self._internalSaved.emit(req_id, new_ver, raw_text)
            except StaleDocumentVersionError as sve:
                self._internalSaveError.emit(req_id, str(sve), True)
            except Exception as e:
                self._internalSaveError.emit(req_id, str(e), False)

        self._executor.submit(_task)

    def save_sync(self) -> bool:
        """Synchronous saver for testing and deterministic inspection."""
        if not self._is_dirty or self._is_saving:
            return False

        if self._has_conflict:
            self._error_message = "Cannot save: document has conflicting external changes. Please reload."
            self.errorChanged.emit()
            return False

        self._request_id += 1
        req_id = self._request_id
        job_id = self._active_job_id
        raw_text = self._source_text
        base_ver = self._active_version

        try:
            new_ver = self.editor_service.commit_source_text(
                job_id=job_id,
                raw_text=raw_text,
                base_version=base_ver,
            )
            self._on_internal_saved(req_id, new_ver, raw_text)
            return True
        except StaleDocumentVersionError as sve:
            self._on_internal_save_error(req_id, str(sve), True)
            return False
        except Exception as e:
            self._on_internal_save_error(req_id, str(e), False)
            return False

    @Slot()
    def discard(self) -> None:
        """Discards uncommitted buffer modifications and reverts to last saved text."""
        self._source_text = self._saved_source_text
        if self._headless_doc is not None and self._headless_doc.toPlainText() != self._source_text:
            self._headless_doc.setPlainText(self._source_text)
        self._is_dirty = False
        self._has_conflict = False
        self._conflict_message = ""
        self._error_message = ""
        self._cursor_line = 1
        self._cursor_column = 1
        self._character_count = len(self._source_text)
        self._word_count = len(re.findall(r"\S+", self._source_text))

        self.sourceTextChanged.emit()
        self.dirtyChanged.emit()
        self.conflictChanged.emit()
        self.errorChanged.emit()
        self.cursorMetricsChanged.emit()
        self.documentMetricsChanged.emit()
        self.discarded.emit()

        if self._search_query:
            self._recompute_matches()
        else:
            self._matches = []
            self._search_total_matches = 0
            self._search_match_index = 0
            self.searchStateChanged.emit()

    @Slot(int)
    def notifyCanonicalDocumentAdvance(self, new_doc_version: int) -> None:
        """
        Receives notification that the active canonical Markdown document advanced to new_doc_version.
        If the editor buffer is dirty, sets conflict state.
        If the buffer is clean, automatically advances activeVersion and reloads the latest document.
        """
        if new_doc_version <= self._active_version:
            return

        if self._is_dirty:
            self._has_conflict = True
            self._conflict_message = (
                f"Canonical document updated to version {new_doc_version} while uncommitted changes exist."
            )
            self.conflictChanged.emit()
            self.conflictDetected.emit(self._conflict_message)
        else:
            self._active_version = new_doc_version
            self.activeVersionChanged.emit()
            if self._active_job_id > 0:
                self.loadSource(self._active_job_id)

    @Slot()
    def clear(self) -> None:
        """Clears all editor state."""
        self._request_id += 1
        self._source_text = ""
        self._saved_source_text = ""
        self._is_dirty = False
        self._is_loading = False
        self._is_saving = False
        self._error_message = ""
        self._active_job_id = 0
        self._active_version = 0
        self._has_conflict = False
        self._conflict_message = ""
        self._cursor_line = 1
        self._cursor_column = 1
        self._character_count = 0
        self._word_count = 0

        self.sourceTextChanged.emit()
        self.dirtyChanged.emit()
        self.loadingChanged.emit()
        self.savingChanged.emit()
        self.errorChanged.emit()
        self.activeJobChanged.emit()
        self.activeVersionChanged.emit()
        self.conflictChanged.emit()
        self.cursorMetricsChanged.emit()
        self.documentMetricsChanged.emit()

        self._search_query = ""
        self._replace_query = ""
        self._search_match_index = 0
        self._search_total_matches = 0
        self._matches = []
        self._is_search_open = False
        self._is_replace_open = False
        self.searchStateChanged.emit()
        self.searchVisibilityChanged.emit()

    @Slot(QObject)
    def attachTextDocument(self, quick_text_doc: Optional[QObject]) -> None:
        """
        Attaches MarkdownSyntaxHighlighter to the underlying QTextDocument of the QML TextArea.
        Attachment is strictly idempotent and prevents duplicate highlighters.
        """
        if self._highlighter is not None:
            try:
                self._highlighter.setDocument(None)
            except RuntimeError:
                pass
            self._highlighter = None
            self._text_document = None

        if quick_text_doc is not None and hasattr(quick_text_doc, "textDocument"):
            doc = quick_text_doc.textDocument()
            self._text_document = doc
            self._highlighter = MarkdownSyntaxHighlighter(doc)

    # -----------------------------------------------------------------------
    # Search and Replace Operations (Qt UTF-16 Coordinate Space)
    # -----------------------------------------------------------------------

    def _get_document(self) -> QTextDocument:
        """Returns attached live QTextDocument or a synchronized headless fallback."""
        if self._text_document is not None:
            return self._text_document
        if self._headless_doc is None:
            self._headless_doc = QTextDocument()
            self._headless_doc.setPlainText(self._source_text)
        return self._headless_doc

    def _recompute_matches(self) -> None:
        """Finds all occurrences of search query using QTextDocument.find() in UTF-16 offsets."""
        if not self._search_query:
            self._matches = []
            self._search_total_matches = 0
            self._search_match_index = 0
            self.searchStateChanged.emit()
            return

        doc = self._get_document()
        flags = QTextDocument.FindFlag(0)
        if self._search_case_sensitive:
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self._search_whole_word:
            flags |= QTextDocument.FindFlag.FindWholeWords

        matches: List[Tuple[int, int]] = []
        pos = 0
        while True:
            cursor = doc.find(self._search_query, pos, flags)
            if cursor.isNull():
                break
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
            matches.append((start, end))
            pos = end

        self._matches = matches
        self._search_total_matches = len(matches)
        if self._search_total_matches == 0:
            self._search_match_index = 0
        elif self._search_match_index == 0 or self._search_match_index > self._search_total_matches:
            self._search_match_index = 1

        self.searchStateChanged.emit()

    @Slot(str)
    def setSearchQuery(self, query: str) -> None:
        """Updates search query and recomputes matches."""
        if self._search_query != query:
            self._search_query = query
            self._recompute_matches()
            if self._search_total_matches > 0 and self._search_match_index > 0:
                active = self._matches[self._search_match_index - 1]
                self.matchSelected.emit(active[0], active[1])

    @Slot(str)
    def setReplaceQuery(self, query: str) -> None:
        """Updates replacement string."""
        if self._replace_query != query:
            self._replace_query = query
            self.searchStateChanged.emit()

    @Slot(bool)
    def setSearchCaseSensitive(self, enabled: bool) -> None:
        """Toggles case sensitivity and re-indexes."""
        if self._search_case_sensitive != enabled:
            self._search_case_sensitive = enabled
            self._recompute_matches()
            if self._search_total_matches > 0 and self._search_match_index > 0:
                active = self._matches[self._search_match_index - 1]
                self.matchSelected.emit(active[0], active[1])

    @Slot(bool)
    def setSearchWholeWord(self, enabled: bool) -> None:
        """Toggles whole-word matching and re-indexes."""
        if self._search_whole_word != enabled:
            self._search_whole_word = enabled
            self._recompute_matches()
            if self._search_total_matches > 0 and self._search_match_index > 0:
                active = self._matches[self._search_match_index - 1]
                self.matchSelected.emit(active[0], active[1])

    @Slot()
    def openSearch(self) -> None:
        """Opens search drawer."""
        self._is_search_open = True
        self.searchVisibilityChanged.emit()
        if self._search_query:
            self._recompute_matches()
            if self._search_total_matches > 0:
                active = self._matches[self._search_match_index - 1]
                self.matchSelected.emit(active[0], active[1])

    @Slot()
    def openReplace(self) -> None:
        """Opens search drawer with replace row visible."""
        self._is_search_open = True
        self._is_replace_open = True
        self.searchVisibilityChanged.emit()
        if self._search_query:
            self._recompute_matches()
            if self._search_total_matches > 0:
                active = self._matches[self._search_match_index - 1]
                self.matchSelected.emit(active[0], active[1])

    @Slot()
    def closeSearch(self) -> None:
        """Closes search drawer."""
        self._is_search_open = False
        self._is_replace_open = False
        self.searchVisibilityChanged.emit()

    @Slot()
    def closeReplace(self) -> None:
        """Closes the replace row in search drawer."""
        self._is_replace_open = False
        self.searchVisibilityChanged.emit()

    @Slot()
    @Slot(int)
    def findNext(self, current_cursor_pos: int = -1) -> None:
        """
        Advances to the next search match using UTF-16 code-unit offsets.
        If current_cursor_pos >= 0 and no match is currently selected, targets the first match on or after pos.
        Wraps around from end to beginning. Emits matchSelected(start, end).
        """
        if self._search_total_matches == 0:
            return

        if current_cursor_pos >= 0 and self._search_match_index == 0:
            target_idx = 0
            for idx, (s, e) in enumerate(self._matches):
                if s >= current_cursor_pos:
                    target_idx = idx
                    break
            self._search_match_index = target_idx + 1
        else:
            self._search_match_index = (self._search_match_index % self._search_total_matches) + 1

        self.searchStateChanged.emit()
        active = self._matches[self._search_match_index - 1]
        self.matchSelected.emit(active[0], active[1])

    @Slot()
    @Slot(int)
    def findPrevious(self, current_cursor_pos: int = -1) -> None:
        """
        Moves to the previous search match using UTF-16 code-unit offsets.
        If current_cursor_pos >= 0 and no match is currently selected, targets the last match before pos.
        Wraps around from beginning to end. Emits matchSelected(start, end).
        """
        if self._search_total_matches == 0:
            return

        if current_cursor_pos >= 0 and self._search_match_index == 0:
            target_idx = self._search_total_matches - 1
            for idx in range(self._search_total_matches - 1, -1, -1):
                if self._matches[idx][1] <= current_cursor_pos:
                    target_idx = idx
                    break
            self._search_match_index = target_idx + 1
        else:
            if self._search_match_index > 1:
                self._search_match_index -= 1
            else:
                self._search_match_index = self._search_total_matches

        self.searchStateChanged.emit()
        active = self._matches[self._search_match_index - 1]
        self.matchSelected.emit(active[0], active[1])

    @Slot(int, int, result=bool)
    def replaceCurrent(self, selection_start: int, selection_end: int) -> bool:
        """
        Replaces the active match if and only if the current selection matches the active search match.
        If the selection does not match, re-targets the active match and returns False without modifying text.
        """
        if self._search_total_matches == 0 or self._search_match_index == 0:
            return False

        active_match = self._matches[self._search_match_index - 1]
        if (selection_start, selection_end) != active_match:
            self.matchSelected.emit(active_match[0], active_match[1])
            return False

        doc = self._get_document()
        cursor = QTextCursor(doc)
        cursor.setPosition(active_match[0])
        cursor.setPosition(active_match[1], QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(self._replace_query)

        repl_len = len(self._replace_query.encode("utf-16-le")) // 2
        next_pos = active_match[0] + repl_len

        self.set_source_text(doc.toPlainText())
        self._recompute_matches()

        if self._search_total_matches > 0:
            target_idx = 0
            for idx, (s, e) in enumerate(self._matches):
                if s >= next_pos:
                    target_idx = idx
                    break
            self._search_match_index = target_idx + 1
            self.searchStateChanged.emit()
            next_match = self._matches[target_idx]
            self.matchSelected.emit(next_match[0], next_match[1])
        else:
            self._search_match_index = 0
            self.searchStateChanged.emit()
        return True

    @Slot(result=int)
    def replaceAll(self) -> int:
        """
        Replaces all occurrences of the active search query using an atomic edit block on QTextDocument.
        Guarantees all replacements collapse into a SINGLE user-level undo action in TextArea.
        """
        if not self._matches or not self._search_query:
            return 0

        doc = self._get_document()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        count = len(self._matches)
        try:
            for s, e in reversed(self._matches):
                c = QTextCursor(doc)
                c.setPosition(s)
                c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
                c.insertText(self._replace_query)
        finally:
            cursor.endEditBlock()

        self.set_source_text(doc.toPlainText())
        self._recompute_matches()
        return count

    @Slot(result="QVariantList")
    def getMatchRanges(self) -> list:
        """Returns match intervals [[start, end], ...] in UTF-16 code units."""
        return [[s, e] for (s, e) in self._matches]

    # -----------------------------------------------------------------------
    # Cursor Position & Document Metrics
    # -----------------------------------------------------------------------

    @Slot(int)
    def updateCursorPosition(self, pos: int) -> None:
        """
        Updates cursorLine and cursorColumn from a Qt UTF-16 code-unit position offset.
        cursorLine: 1-indexed document block (line) number.
        cursorColumn: 1-indexed UTF-16 code-unit offset within the block.
        """
        doc = self._get_document()
        cursor = QTextCursor(doc)
        doc_len = max(0, doc.characterCount() - 1)
        clamped_pos = max(0, min(pos, doc_len))
        cursor.setPosition(clamped_pos)
        new_line = cursor.blockNumber() + 1
        new_col = cursor.positionInBlock() + 1
        if self._cursor_line != new_line or self._cursor_column != new_col:
            self._cursor_line = new_line
            self._cursor_column = new_col
            self.cursorMetricsChanged.emit()

    def shutdown(self) -> None:
        """Shuts down background thread executor and detaches syntax highlighter."""
        self._is_shutdown = True
        if self._highlighter is not None:
            try:
                self._highlighter.setDocument(None)
            except RuntimeError:
                pass
            self._highlighter = None
        self._text_document = None
        self._headless_doc = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -----------------------------------------------------------------------
    # Internal Handlers (GUI Thread)
    # -----------------------------------------------------------------------

    def _on_internal_loaded(self, req_id: int, text: str, version: int) -> None:
        if req_id != self._request_id or self._is_shutdown:
            return

        self._source_text = text
        self._saved_source_text = text
        self._active_version = version
        self._is_dirty = False
        self._is_loading = False
        self._error_message = ""
        self._has_conflict = False
        self._conflict_message = ""
        self._cursor_line = 1
        self._cursor_column = 1
        self._character_count = len(self._source_text)
        self._word_count = len(re.findall(r"\S+", self._source_text))
        if self._headless_doc is not None:
            self._headless_doc.setPlainText(text)

        self.sourceTextChanged.emit()
        self.dirtyChanged.emit()
        self.activeVersionChanged.emit()
        self.loadingChanged.emit()
        self.errorChanged.emit()
        self.conflictChanged.emit()
        self.cursorMetricsChanged.emit()
        self.documentMetricsChanged.emit()

        if self._search_query:
            self._recompute_matches()
        else:
            self._matches = []
            self._search_total_matches = 0
            self._search_match_index = 0
            self.searchStateChanged.emit()

    def _on_internal_load_error(self, req_id: int, error_msg: str) -> None:
        if req_id != self._request_id or self._is_shutdown:
            return

        self._is_loading = False
        self._error_message = error_msg
        self.loadingChanged.emit()
        self.errorChanged.emit()

    def _on_internal_saved(self, req_id: int, new_version: int, saved_text: str) -> None:
        if req_id != self._request_id or self._is_shutdown:
            return

        self._saved_source_text = saved_text
        self._active_version = new_version
        self._is_dirty = (self._source_text != self._saved_source_text)
        self._is_saving = False
        self._error_message = ""
        self._has_conflict = False
        self._conflict_message = ""

        self.activeVersionChanged.emit()
        self.dirtyChanged.emit()
        self.savingChanged.emit()
        self.errorChanged.emit()
        self.conflictChanged.emit()
        self.saved.emit(new_version)

    def _on_internal_save_error(self, req_id: int, error_msg: str, is_conflict: bool) -> None:
        if req_id != self._request_id or self._is_shutdown:
            return

        self._is_saving = False
        self._error_message = error_msg
        if is_conflict:
            self._has_conflict = True
            self._conflict_message = error_msg
            self.conflictChanged.emit()
            self.conflictDetected.emit(error_msg)

        self.savingChanged.emit()
        self.errorChanged.emit()
