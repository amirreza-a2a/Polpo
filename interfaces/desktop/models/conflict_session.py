# ============================================================
#  interfaces/desktop/models/conflict_session.py
#  Presentation model managing active three-way merge conflict hunks
# ============================================================

from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Set, Tuple
from interfaces.desktop.qt_compat import Property, QObject, Signal, Slot
from application.dtos.merge_dto import ConflictHunkDTO, MergeAnalysisResultDTO


@dataclass(frozen=True)
class HunkMarkerSpan:
    hunk_index: int
    start_pos: int
    end_pos: int
    local_text: str
    remote_text: str
    malformed: bool = False


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

        self._original_local_texts: Dict[int, str] = {
            h.hunk_index: h.local_text for h in self._conflict_hunks
        }
        self._original_remote_texts: Dict[int, str] = {
            h.hunk_index: h.remote_text for h in self._conflict_hunks
        }
        self._current_local_texts: Dict[int, str] = dict(self._original_local_texts)
        self._current_remote_texts: Dict[int, str] = dict(self._original_remote_texts)
        self._unresolved_hunk_indices_in_buffer: Set[int] = {
            h.hunk_index for h in self._conflict_hunks
        }
        self._has_malformed_markers: bool = False
        self._last_synced_buffer: str = ""

    # =========================================================================
    # Properties
    # =========================================================================

    @Property(bool, notify=sessionChanged)
    def hasMalformedMarkers(self) -> bool:
        return self._has_malformed_markers

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

    def _find_marker_span(self, text: str, hunk_index: int) -> Optional[HunkMarkerSpan]:
        start_tag = f"<<<<<<< [LOCAL:hunk_{hunk_index}]"
        end_tag = f">>>>>>> [CANONICAL:hunk_{hunk_index}]"

        pos_start = text.find(start_tag)
        pos_end = text.find(end_tag)

        if pos_start == -1 and pos_end == -1:
            return None

        if pos_start == -1 or pos_end == -1 or pos_end <= pos_start:
            return HunkMarkerSpan(
                hunk_index=hunk_index,
                start_pos=-1,
                end_pos=-1,
                local_text="",
                remote_text="",
                malformed=True,
            )

        line_start = text.rfind("\n", 0, pos_start)
        line_start = 0 if line_start == -1 else line_start + 1

        line_end = text.find("\n", pos_start)
        if line_end == -1:
            return HunkMarkerSpan(
                hunk_index=hunk_index,
                start_pos=-1,
                end_pos=-1,
                local_text="",
                remote_text="",
                malformed=True,
            )
        local_start = line_end + 1

        sub = text[local_start:pos_end]
        divider_match = re.search(r"^[ \t]*={7}[ \t]*$", sub, re.MULTILINE)
        if not divider_match:
            return HunkMarkerSpan(
                hunk_index=hunk_index,
                start_pos=-1,
                end_pos=-1,
                local_text="",
                remote_text="",
                malformed=True,
            )

        div_line_start = local_start + divider_match.start()
        div_line_end = local_start + divider_match.end()
        if div_line_end < len(text) and text[div_line_end] == "\r":
            div_line_end += 1
        if div_line_end < len(text) and text[div_line_end] == "\n":
            div_line_end += 1

        local_text = text[local_start:div_line_start]
        if local_text.endswith("\r\n"):
            local_text = local_text[:-2]
        elif local_text.endswith("\n"):
            local_text = local_text[:-1]
        orig_local = self._original_local_texts.get(hunk_index, "")
        if orig_local.endswith(("\n", "\r\n")):
            local_text += "\r\n" if orig_local.endswith("\r\n") else "\n"

        remote_start = div_line_end

        end_tag_line_start = text.rfind("\n", remote_start, pos_end)
        end_tag_line_start = remote_start if end_tag_line_start == -1 else end_tag_line_start + 1
        remote_text = text[remote_start:end_tag_line_start]
        if remote_text.endswith("\r\n"):
            remote_text = remote_text[:-2]
        elif remote_text.endswith("\n"):
            remote_text = remote_text[:-1]
        orig_remote = self._original_remote_texts.get(hunk_index, "")
        if orig_remote.endswith(("\n", "\r\n")):
            remote_text += "\r\n" if orig_remote.endswith("\r\n") else "\n"

        end_tag_line_end = text.find("\n", pos_end)
        if end_tag_line_end == -1:
            end_pos = len(text)
        else:
            end_pos = end_tag_line_end + 1

        return HunkMarkerSpan(
            hunk_index=hunk_index,
            start_pos=line_start,
            end_pos=end_pos,
            local_text=local_text,
            remote_text=remote_text,
            malformed=False,
        )

    @Slot(str)
    def sync_from_buffer(self, text: str) -> None:
        """
        Synchronizes active conflict state from the editor buffer text.
        Tracks in-buffer manual edits, detects manual marker removals,
        and validates marker syntax integrity.
        """
        self._last_synced_buffer = text
        malformed = False
        unresolved_in_buffer: Set[int] = set()

        for hunk in self._conflict_hunks:
            idx = hunk.hunk_index
            span = self._find_marker_span(text, idx)
            if span is None:
                # No markers found for this hunk in buffer
                # If not recorded as resolved yet, user manually deleted markers
                if idx not in self._resolutions:
                    self._resolutions[idx] = "<manual>"
            elif span.malformed:
                malformed = True
            else:
                unresolved_in_buffer.add(idx)
                self._current_local_texts[idx] = span.local_text
                self._current_remote_texts[idx] = span.remote_text
                # If markers were restored (e.g. undo), remove recorded resolution
                if idx in self._resolutions:
                    del self._resolutions[idx]

        all_local_tags = re.findall(r"^[ \t]*<{7} \[LOCAL:hunk_(\d+)\]", text, re.MULTILINE)
        all_remote_tags = re.findall(r"^[ \t]*>{7} \[CANONICAL:hunk_(\d+)\]", text, re.MULTILINE)
        valid_indices = {str(h.hunk_index) for h in self._conflict_hunks}
        for tid in all_local_tags:
            if tid not in valid_indices or tid not in all_remote_tags:
                malformed = True
        for tid in all_remote_tags:
            if tid not in valid_indices or tid not in all_local_tags:
                malformed = True

        self._has_malformed_markers = malformed
        self._unresolved_hunk_indices_in_buffer = unresolved_in_buffer

        self.sessionChanged.emit()
        self.canSaveChanged.emit(self.is_fully_resolved())

    def apply_resolution_to_buffer(
        self, buffer_text: str, hunk_index: int, choice: str, custom_text: Optional[str] = None
    ) -> str:
        """
        Applies a resolution choice directly to the buffer text, replacing only the
        target conflict hunk's marker span and preserving all other edits in the document.
        """
        self.sync_from_buffer(buffer_text)

        choice_key = choice.lower().strip()
        hunk = self._hunks_by_index.get(hunk_index)

        span = self._find_marker_span(buffer_text, hunk_index)
        is_span_present = span is not None and not span.malformed

        if choice_key == "local":
            resolved_text = (
                self._current_local_texts.get(hunk_index, hunk.local_text if hunk else "")
                if is_span_present
                else self._original_local_texts.get(hunk_index, hunk.local_text if hunk else "")
            )
        elif choice_key in ("remote", "incoming"):
            resolved_text = (
                self._current_remote_texts.get(hunk_index, hunk.remote_text if hunk else "")
                if is_span_present
                else self._original_remote_texts.get(hunk_index, hunk.remote_text if hunk else "")
            )
        elif choice_key == "both":
            local = (
                self._current_local_texts.get(hunk_index, hunk.local_text if hunk else "")
                if is_span_present
                else self._original_local_texts.get(hunk_index, hunk.local_text if hunk else "")
            )
            remote = (
                self._current_remote_texts.get(hunk_index, hunk.remote_text if hunk else "")
                if is_span_present
                else self._original_remote_texts.get(hunk_index, hunk.remote_text if hunk else "")
            )
            if hunk and hunk.base_text and not local and remote:
                resolved_text = remote
            else:
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

        if is_span_present and span is not None:
            if span.end_pos == len(buffer_text) and not buffer_text.endswith(("\n", "\r\n")):
                pass
            elif (
                resolved_text
                and not resolved_text.endswith(("\n", "\r\n"))
                and span.end_pos < len(buffer_text)
            ):
                resolved_text = resolved_text + "\n"
            new_buffer = buffer_text[:span.start_pos] + resolved_text + buffer_text[span.end_pos:]
        else:
            prev_res = self._resolutions.get(hunk_index)
            if prev_res and prev_res in buffer_text:
                pos = buffer_text.find(prev_res)
                new_buffer = buffer_text[:pos] + resolved_text + buffer_text[pos + len(prev_res):]
            else:
                new_buffer = buffer_text

        self._resolutions[hunk_index] = resolved_text
        self.sync_from_buffer(new_buffer)
        return new_buffer

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
            resolved_text = self._current_local_texts.get(hunk_index, hunk.local_text)
        elif choice_key in ("remote", "incoming"):
            resolved_text = self._current_remote_texts.get(hunk_index, hunk.remote_text)
        elif choice_key == "both":
            if hunk.base_text and not hunk.local_text and hunk.remote_text:
                resolved_text = self._current_remote_texts.get(hunk_index, hunk.remote_text)
            else:
                local = self._current_local_texts.get(hunk_index, hunk.local_text)
                remote = self._current_remote_texts.get(hunk_index, hunk.remote_text)
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
        if hasattr(self, "_unresolved_hunk_indices_in_buffer"):
            self._unresolved_hunk_indices_in_buffer.discard(hunk_index)
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
        Returns True if every conflict hunk has a recorded resolution and no malformed
        or unresolved conflict markers remain in the buffer.
        """
        if getattr(self, "_has_malformed_markers", False):
            return False
        if not self._conflict_hunks:
            return True
        if getattr(self, "_unresolved_hunk_indices_in_buffer", None):
            return False
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
                if hunk.hunk_index in self._resolutions and self._resolutions[hunk.hunk_index] != "<manual>":
                    resolved = self._resolutions[hunk.hunk_index]
                    if resolved:
                        lines.extend(resolved.splitlines())
                else:
                    lines.append(f"<<<<<<< [LOCAL:hunk_{hunk.hunk_index}]")
                    local = self._current_local_texts.get(hunk.hunk_index, hunk.local_text)
                    if local:
                        lines.extend(local.splitlines())
                    lines.append("=======")
                    remote = self._current_remote_texts.get(hunk.hunk_index, hunk.remote_text)
                    if remote:
                        lines.extend(remote.splitlines())
                    lines.append(f">>>>>>> [CANONICAL:hunk_{hunk.hunk_index}]")
            else:
                clean_text = _clean_hunk_text(hunk)
                if clean_text:
                    lines.extend(clean_text.splitlines())

        if not lines:
            res = ""
        else:
            trail = self._has_trailing_newline()
            joined = "\n".join(lines)
            res = joined + "\n" if trail else joined

        self._last_synced_buffer = res
        self.sync_from_buffer(res)
        return res

    def generate_candidate_markdown(self, current_buffer: Optional[str] = None) -> str:
        """
        Generates the clean candidate Markdown text.
        Guarantees ZERO conflict markers: unresolved hunks fall back to local edits.
        Preserves all manual edits in the buffer.
        """
        buf = (
            current_buffer
            if current_buffer is not None
            else getattr(self, "_last_synced_buffer", "")
        )
        if buf:
            if buf != getattr(self, "_last_synced_buffer", ""):
                self.sync_from_buffer(buf)
            candidate = buf
            spans: List[HunkMarkerSpan] = []
            for h in self._conflict_hunks:
                span = self._find_marker_span(candidate, h.hunk_index)
                if span is not None and not span.malformed:
                    spans.append(span)
            spans.sort(key=lambda s: s.start_pos, reverse=True)
            for s in spans:
                if s.hunk_index in self._resolutions and self._resolutions[s.hunk_index] != "<manual>":
                    replacement = self._resolutions[s.hunk_index]
                else:
                    replacement = self._current_local_texts.get(s.hunk_index, s.local_text)
                candidate = candidate[:s.start_pos] + replacement + candidate[s.end_pos:]
            return candidate

        if not self._conflict_hunks and self._analysis_result.clean_text is not None:
            return self._analysis_result.clean_text

        lines: List[str] = []
        for hunk in self._all_hunks:
            ht = (hunk.hunk_type or "").upper()
            if ht == "CONFLICT":
                if hunk.hunk_index in self._resolutions and self._resolutions[hunk.hunk_index] != "<manual>":
                    resolved = self._resolutions[hunk.hunk_index]
                    if resolved:
                        lines.extend(resolved.splitlines())
                else:
                    # D06 invariant: unresolved hunks fall back to user's local edits
                    local = self._current_local_texts.get(hunk.hunk_index, hunk.local_text)
                    if local:
                        lines.extend(local.splitlines())
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
