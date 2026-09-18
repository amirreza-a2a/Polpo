# ============================================================
#  interfaces/desktop/models/conflict_session.py
#  Presentation model managing active three-way merge conflict hunks
# ============================================================

from typing import Dict, List, Optional, Tuple
from interfaces.desktop.qt_compat import Property, QObject, Signal, Slot
from application.dtos.merge_dto import ConflictHunkDTO, MergeAnalysisResultDTO


def _clean_hunk_text(hunk: ConflictHunkDTO) -> str:
    """
    Extracts the authoritative text representation for a non-conflicting hunk.
    """
    ht = (hunk.hunk_type or "").upper()
    if ht == "CLEAN_REMOTE":
        return hunk.remote_text
    elif ht in ("CLEAN_LOCAL", "CLEAN_SAME", "CLEAN_UNCHANGED"):
        return hunk.local_text

    if hunk.local_text:
        return hunk.local_text
    if hunk.remote_text and "REMOTE" in ht:
        return hunk.remote_text
    return hunk.local_text or hunk.base_text


class ConflictSession(QObject):
    """
    Presentation model managing active conflict hunks, user resolution selections,
    and cursor navigation across conflict boundaries.
    """

    sessionChanged = Signal()
    currentHunkIndexChanged = Signal(int)
    canSaveChanged = Signal(bool)
    invalidated = Signal()

    def __init__(
        self,
        job_id: int,
        merge_session_id: int,
        base_version: int,
        canonical_version: int,
        analysis_result: MergeAnalysisResultDTO,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._job_id: int = job_id
        self._session_id: int = merge_session_id
        self._base_version: int = base_version
        self._canonical_version: int = canonical_version
        self._analysis_result: MergeAnalysisResultDTO = analysis_result

        self._all_hunks: Tuple[ConflictHunkDTO, ...] = (
            tuple(analysis_result.hunks) if analysis_result.hunks else ()
        )
        self._conflict_hunks: List[ConflictHunkDTO] = [
            h for h in self._all_hunks if (h.hunk_type or "").upper() == "CONFLICT"
        ]
        self._hunks_by_index: Dict[int, ConflictHunkDTO] = {
            h.hunk_index: h for h in self._all_hunks
        }

        self._resolutions: Dict[int, str] = {}
        self._current_hunk_index: int = 0 if self._conflict_hunks else -1
        self._is_invalidated: bool = False

    # =========================================================================
    # Properties
    # =========================================================================

    @Property(int, notify=currentHunkIndexChanged)
    def currentHunkIndex(self) -> int:
        return self._current_hunk_index

    @Property(int, notify=sessionChanged)
    def totalConflicts(self) -> int:
        return len(self._conflict_hunks)

    @Property(str, notify=currentHunkIndexChanged)
    def currentConflictLabel(self) -> str:
        if (
            not self._conflict_hunks
            or self._current_hunk_index < 0
            or self._current_hunk_index >= len(self._conflict_hunks)
        ):
            return ""

        hunk = self._conflict_hunks[self._current_hunk_index]
        total = len(self._conflict_hunks)
        idx_display = self._current_hunk_index + 1
        if hunk.ast_label:
            return f"Conflict {idx_display} of {total}: {hunk.ast_label}"
        return f"Conflict {idx_display} of {total}"

    @Property(bool, notify=canSaveChanged)
    def canSave(self) -> bool:
        return self.is_fully_resolved()

    @Property(bool, notify=sessionChanged)
    def isInvalidated(self) -> bool:
        return self._is_invalidated

    @Property(int, constant=True)
    def canonicalVersion(self) -> int:
        return self._canonical_version

    @Property(int, constant=True)
    def baseVersion(self) -> int:
        return self._base_version

    @Property(int, constant=True)
    def mergeSessionId(self) -> int:
        return self._session_id

    # =========================================================================
    # Slots & Methods
    # =========================================================================

    @Slot(int, str)
    @Slot(int, str, str)
    def resolve_hunk(
        self, hunk_index: int, choice: str, custom_text: Optional[str] = None
    ) -> None:
        """
        Records the user resolution for a specific conflict hunk.
        Supports 'local', 'remote'/'incoming', 'both', or 'custom'.
        """
        hunk = self._hunks_by_index.get(hunk_index)
        if hunk is None:
            return

        choice_key = choice.lower().strip()
        if choice_key == "local":
            resolved_text = hunk.local_text
        elif choice_key in ("remote", "incoming"):
            resolved_text = hunk.remote_text
        elif choice_key == "both":
            # Delete vs Modify collision: if local deleted the base text and remote modified it,
            # accepting both retains remote modifications.
            if hunk.base_text and not hunk.local_text and hunk.remote_text:
                resolved_text = hunk.remote_text
            else:
                local = hunk.local_text
                remote = hunk.remote_text
                if local and remote:
                    if local.endswith(("\n", "\r\n")):
                        resolved_text = local + remote
                    else:
                        resolved_text = local + "\n" + remote
                elif local:
                    resolved_text = local
                else:
                    resolved_text = remote
        elif choice_key == "custom":
            resolved_text = custom_text if custom_text is not None else ""
        else:
            resolved_text = custom_text if custom_text is not None else choice

        self._resolutions[hunk_index] = resolved_text
        self.sessionChanged.emit()
        self.canSaveChanged.emit(self.is_fully_resolved())

    @Slot(result=int)
    def next_hunk(self) -> int:
        """
        Advances to the next conflict hunk index if available.
        """
        if self._current_hunk_index < len(self._conflict_hunks) - 1:
            self._current_hunk_index += 1
            self.currentHunkIndexChanged.emit(self._current_hunk_index)
        return self._current_hunk_index

    @Slot(result=int)
    def prev_hunk(self) -> int:
        """
        Reverses to the previous conflict hunk index if available.
        """
        if self._current_hunk_index > 0:
            self._current_hunk_index -= 1
            self.currentHunkIndexChanged.emit(self._current_hunk_index)
        return self._current_hunk_index

    def is_fully_resolved(self) -> bool:
        """
        Returns True if every conflict hunk has a recorded resolution.
        """
        if not self._conflict_hunks:
            return True
        return all(h.hunk_index in self._resolutions for h in self._conflict_hunks)

    def get_current_hunk(self) -> Optional[ConflictHunkDTO]:
        """
        Returns the active ConflictHunkDTO based on currentHunkIndex.
        """
        if 0 <= self._current_hunk_index < len(self._conflict_hunks):
            return self._conflict_hunks[self._current_hunk_index]
        return None

    @Slot()
    def invalidate(self) -> None:
        """
        Marks this conflict session as obsolete due to a concurrent external advance.
        """
        self._is_invalidated = True
        self.invalidated.emit()
        self.sessionChanged.emit()

    def generate_in_buffer_markdown(self) -> str:
        """
        Generates the in-buffer document representation containing collision-safe
        tagged conflict markers for unresolved hunks.
        """
        lines: List[str] = []
        for hunk in self._all_hunks:
            ht = (hunk.hunk_type or "").upper()
            if ht == "CONFLICT":
                if hunk.hunk_index in self._resolutions:
                    resolved = self._resolutions[hunk.hunk_index]
                    if resolved:
                        lines.extend(resolved.splitlines())
                else:
                    lines.append(f"<<<<<<< [LOCAL:hunk_{hunk.hunk_index}]")
                    if hunk.local_text:
                        lines.extend(hunk.local_text.splitlines())
                    lines.append("=======")
                    if hunk.remote_text:
                        lines.extend(hunk.remote_text.splitlines())
                    lines.append(f">>>>>>> [CANONICAL:hunk_{hunk.hunk_index}]")
            else:
                clean_text = _clean_hunk_text(hunk)
                if clean_text:
                    lines.extend(clean_text.splitlines())

        if not lines:
            return ""

        trail = self._has_trailing_newline()
        joined = "\n".join(lines)
        return joined + "\n" if trail else joined

    def generate_candidate_markdown(self) -> str:
        """
        Generates the clean candidate Markdown text.
        Guarantees ZERO conflict markers: unresolved hunks fall back to local edits.
        """
        if not self._conflict_hunks and self._analysis_result.clean_text is not None:
            return self._analysis_result.clean_text

        lines: List[str] = []
        for hunk in self._all_hunks:
            ht = (hunk.hunk_type or "").upper()
            if ht == "CONFLICT":
                if hunk.hunk_index in self._resolutions:
                    resolved = self._resolutions[hunk.hunk_index]
                    if resolved:
                        lines.extend(resolved.splitlines())
                else:
                    # D06 invariant: unresolved hunks fall back to user's local edits
                    if hunk.local_text:
                        lines.extend(hunk.local_text.splitlines())
            else:
                clean_text = _clean_hunk_text(hunk)
                if clean_text:
                    lines.extend(clean_text.splitlines())

        if not lines:
            return ""

        trail = self._has_trailing_newline()
        joined = "\n".join(lines)
        return joined + "\n" if trail else joined

    def _has_trailing_newline(self) -> bool:
        """
        Checks whether the terminal hunk or document preserved a trailing newline.
        """
        if not self._all_hunks:
            return False

        last_hunk = self._all_hunks[-1]
        ht = (last_hunk.hunk_type or "").upper()
        if ht == "CONFLICT":
            if last_hunk.hunk_index in self._resolutions:
                return self._resolutions[last_hunk.hunk_index].endswith(("\n", "\r\n"))
            return last_hunk.local_text.endswith(("\n", "\r\n"))
        clean_text = _clean_hunk_text(last_hunk)
        return clean_text.endswith(("\n", "\r\n"))
