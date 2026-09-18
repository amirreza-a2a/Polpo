# ============================================================
#  application/services/markdown_merge_service.py
#  Application Service for Diff3 Markdown Three-Way Merge Analysis
# ============================================================

import html
import re
from typing import List

from application.dtos.merge_dto import ConflictHunkDTO, MergeAnalysisResultDTO
from application.ports.storage import IArtifactStorage
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.services.markdown_viewer_service import MarkdownViewerService
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.markdown.merge import three_way_merge


class MarkdownMergeService:
    """
    Application service performing synchronous, non-destructive three-way merge analysis
    between a base document version, local uncommitted editor text, and the canonical remote version.

    Zero-write invariant: Never commits to SQLite, never stores files to disk.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        storage: IArtifactStorage,
        viewer_service: MarkdownViewerService,
    ):
        self.uow_factory = uow_factory
        self.storage = storage
        self.viewer_service = viewer_service

    def analyze_three_way_merge(
        self,
        job_id: int,
        base_version: int,
        local_text: str,
        canonical_version: int,
        merge_session_id: int,
    ) -> MergeAnalysisResultDTO:
        """
        Performs three-way diff3 merge calculation and AST-label enrichment without
        mutating persistent application state (zero-write invariant).
        """
        # 1. Retrieve base document text
        base_text = ""
        if base_version > 0:
            base_handle = ArtifactHandle(
                storage_backend=StorageBackendType.LOCAL_FS,
                uri="",
                artifact_type=getattr(ArtifactType, "TRANSCRIPTION", ArtifactType.OUTPUT_MARKDOWN),
                job_id=job_id,
                filename=f"output_{job_id}_v{base_version}.md",
            )
            if self.storage.exists(base_handle):
                try:
                    base_bytes = self.storage.retrieve(base_handle)
                    base_text = base_bytes.decode("utf-8")
                except Exception:
                    base_text = ""

        # 2. Retrieve canonical document text
        canonical_text = ""
        if canonical_version > 0:
            canonical_handle = ArtifactHandle(
                storage_backend=StorageBackendType.LOCAL_FS,
                uri="",
                artifact_type=getattr(ArtifactType, "TRANSCRIPTION", ArtifactType.OUTPUT_MARKDOWN),
                job_id=job_id,
                filename=f"output_{job_id}_v{canonical_version}.md",
            )
            if self.storage.exists(canonical_handle):
                try:
                    canonical_bytes = self.storage.retrieve(canonical_handle)
                    canonical_text = canonical_bytes.decode("utf-8")
                except Exception:
                    canonical_text = ""

        # 3. Execute core three-way diff3 merge calculation
        merge_result = three_way_merge(
            base_text=base_text,
            local_text=local_text,
            remote_text=canonical_text,
        )

        # 4. AST Label Enrichment
        doc_dto = None
        try:
            doc_dto = self.viewer_service.render_text(
                raw_text=local_text,
                active_regions=(),
                job_id=job_id,
                version=base_version,
            )
        except Exception:
            doc_dto = None

        hunk_dtos: List[ConflictHunkDTO] = []
        for hunk in merge_result.hunks:
            target_line = hunk.local_line_start + 1
            ast_label = ""

            matched_node = None
            if doc_dto and doc_dto.nodes:
                # 1. Containment check on target_line
                for node in doc_dto.nodes:
                    if node.source_start_line is not None and node.source_end_line is not None:
                        if node.source_start_line <= target_line <= node.source_end_line:
                            matched_node = node
                            break
                    elif node.source_start_line is not None and node.source_start_line == target_line:
                        matched_node = node
                        break

                # 2. Overlap check with hunk range if not matched
                if matched_node is None:
                    h_end = max(target_line, hunk.local_line_end)
                    for node in doc_dto.nodes:
                        if node.source_start_line is not None and node.source_end_line is not None:
                            if max(node.source_start_line, target_line) <= min(node.source_end_line, h_end):
                                matched_node = node
                                break

            if matched_node is not None:
                clean_content = re.sub(r"<[^>]+>", "", matched_node.content or "").strip()
                clean_content = html.unescape(clean_content)

                if matched_node.node_type == "heading":
                    label_content = clean_content or matched_node.content
                    ast_label = f"Heading {matched_node.level}: {label_content}"
                elif matched_node.regions and len(matched_node.regions) > 0 and matched_node.regions[0].region_id:
                    ast_label = f"Visual Region: {matched_node.regions[0].region_id}"
                elif matched_node.node_type == "paragraph":
                    label_content = clean_content or matched_node.content
                    if label_content:
                        ast_label = f"Paragraph: {label_content[:30]}"
                    else:
                        ast_label = "Paragraph"
                elif matched_node.node_type:
                    ast_label = matched_node.node_type.capitalize()
                else:
                    ast_label = f"Line {target_line}"
            else:
                ast_label = f"Line {target_line}"

            hunk_type_str = hunk.hunk_type.name if hasattr(hunk.hunk_type, "name") else str(hunk.hunk_type)
            hunk_dto = ConflictHunkDTO(
                hunk_index=hunk.hunk_index,
                hunk_type=hunk_type_str,
                base_text="\n".join(hunk.base_lines),
                local_text="\n".join(hunk.local_lines),
                remote_text="\n".join(hunk.remote_lines),
                local_line_start=hunk.local_line_start,
                local_line_end=hunk.local_line_end,
                ast_label=ast_label,
            )
            hunk_dtos.append(hunk_dto)

        return MergeAnalysisResultDTO(
            job_id=job_id,
            merge_session_id=merge_session_id,
            base_version=base_version,
            canonical_version=canonical_version,
            has_conflicts=merge_result.has_conflicts,
            clean_text=merge_result.clean_text,
            hunks=tuple(hunk_dtos),
            conflict_count=merge_result.conflict_count,
            auto_merged_count=merge_result.auto_merged_count,
            canonical_text=canonical_text,
        )
