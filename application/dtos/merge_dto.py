# ============================================================
#  application/dtos/merge_dto.py
#  Transport-neutral DTOs for Three-Way Merge Analysis
# ============================================================

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ConflictHunkDTO:
    """
    Presentation DTO representing a single diff3 merge hunk or conflict hunk.
    """
    hunk_index: int
    hunk_type: str
    base_text: str
    local_text: str
    remote_text: str
    local_line_start: int
    local_line_end: int
    ast_label: str = ""


@dataclass(frozen=True)
class MergeAnalysisResultDTO:
    """
    Analysis result DTO produced by MarkdownMergeService.
    Encapsulates merge status, clean merged text (if clean), and AST-enriched hunks.
    """
    job_id: int
    merge_session_id: int
    base_version: int
    canonical_version: int
    has_conflicts: bool
    clean_text: Optional[str]
    hunks: Tuple[ConflictHunkDTO, ...]
    conflict_count: int
    auto_merged_count: int
    canonical_text: Optional[str] = None
