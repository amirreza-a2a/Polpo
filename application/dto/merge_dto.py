# ============================================================
#  application/dto/merge_dto.py
#  Re-export of merge DTOs for backwards-compatible singular package import
# ============================================================

from application.dtos.merge_dto import ConflictHunkDTO, MergeAnalysisResultDTO

__all__ = [
    "ConflictHunkDTO",
    "MergeAnalysisResultDTO",
]
