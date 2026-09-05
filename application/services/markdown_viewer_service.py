# ============================================================
#  application/services/markdown_viewer_service.py
#  Application Service for Markdown Document AST Loading & Projection
# ============================================================

import hashlib
import html
import os
import re
from collections import defaultdict
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from application.dto.markdown_dto import (
    InlineSegmentDTO,
    MarkdownDocumentDTO,
    MarkdownNodeDTO,
    QuoteChildBlockDTO,
    RegionOccurrenceRef,
    VisualRegionRefDTO,
)
from application.ports.markdown_parser import IMarkdownParser
from application.ports.storage import IArtifactStorage
from application.ports.unit_of_work import IUnitOfWorkFactory
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.entities.visual_region import VisualRegion
from core.exceptions.domain_exceptions import ArtifactNotFoundError, DomainError, EntityNotFoundError
from core.markdown.ast import (
    BlockquoteBlock,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    InlineSpan,
    InlineType,
    ListBlock,
    MarkdownBlock,
    ParagraphBlock,
    TableFallbackBlock,
    ThematicBreakBlock,
)
from core.markdown.resolver import resolve_image_regions


def _slugify(text: str) -> str:
    """Derives a clean URL/identifier-safe slug from plain text."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text or "heading"


def _normalize_whitespace(text: str) -> str:
    """Collapses consecutive whitespace characters into a single space."""
    return re.sub(r"\s+", " ", text).strip()


def _is_safe_url(url: Optional[str]) -> bool:
    """
    Validates hyperlink URLs against allowed safe external web schemes.
    Explicitly excludes file://, region://, javascript:, and data: schemes.
    Internal region:// links are synthesized strictly by the DTO layer for active visual regions.
    """
    if not url:
        return False
    u = url.strip().lower()
    return (
        u.startswith("http://")
        or u.startswith("https://")
        or u.startswith("#")
    )


class MarkdownViewerService:
    """
    Application Service responsible for loading, parsing, resolving visual regions,
    and projecting canonical Markdown documents into presentation DTOs.

    Guarantees:
      - 100% independent from Qt, PySide6, and SQLite persistence implementations.
      - Generates deterministic, collision-free node IDs invariant to unrelated block insertion.
      - Sanitizes RichText HTML according to the strict tag whitelist.
      - Synthesizes internal region:// links strictly for verified active visual regions.
      - Builds an O(1) reverse lookup index from visual region UUID to document occurrences.
    """

    def __init__(
        self,
        parser: IMarkdownParser,
        uow_factory: IUnitOfWorkFactory,
        storage: IArtifactStorage,
    ):
        self.parser = parser
        self.uow_factory = uow_factory
        self.storage = storage

    def load_document(self, job_id: int) -> MarkdownDocumentDTO:
        """
        Loads the active committed Markdown artifact for a job, resolves visual regions,
        and returns the complete MarkdownDocumentDTO presentation representation.
        """
        with self.uow_factory.create() as uow:
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            if not job.output_path:
                raise DomainError(f"Job {job_id} has no committed output Markdown artifact.")

            active_regions = uow.visual_regions.get_by_job_id(job_id)

        # Extract version number from output path (e.g. output_123_v2.md)
        ver_match = re.search(r"_v(\d+)\.md$", job.output_path)
        version = int(ver_match.group(1)) if ver_match else 1

        filename = os.path.basename(job.output_path)
        output_handle = ArtifactHandle(
            storage_backend=StorageBackendType.LOCAL_FS,
            uri=job.output_path,
            artifact_type=ArtifactType.OUTPUT_MARKDOWN,
            job_id=job_id,
            filename=filename,
        )

        try:
            raw_bytes = self.storage.retrieve(output_handle)
        except (ArtifactNotFoundError, IOError, OSError) as e:
            raise DomainError(
                f"Cannot load Markdown for job {job_id}: Artifact '{filename}' missing ({e})."
            ) from e

        raw_text = raw_bytes.decode("utf-8")
        clean_path = job.output_path[7:] if job.output_path.startswith("file://") else job.output_path
        base_dir = os.path.dirname(clean_path) if clean_path else None

        return self.render_text(
            raw_text=raw_text,
            active_regions=active_regions,
            job_id=job_id,
            version=version,
            base_dir=base_dir,
        )

    def render_text(
        self,
        raw_text: str,
        active_regions: Sequence[VisualRegion],
        job_id: int,
        version: int = 1,
        base_dir: Optional[str] = None,
    ) -> MarkdownDocumentDTO:
        """
        Pure projection: parses raw Markdown text, resolves visual regions,
        and constructs the complete presentation DTO tree.
        """
        doc = self.parser.parse(raw_text)
        resolved_doc = resolve_image_regions(doc, active_regions, job_id)

        regions_by_id = {
            r.region_id: r
            for r in active_regions
            if r.region_id and not r.is_deleted
        }

        seen_counts: Dict[Tuple[str, ...], int] = defaultdict(int)
        nodes: List[MarkdownNodeDTO] = []
        region_to_occurrences: Dict[str, List[RegionOccurrenceRef]] = defaultdict(list)

        for node_idx, block in enumerate(resolved_doc.blocks):
            node_dto = self._project_block(
                block=block,
                node_idx=node_idx,
                seen_counts=seen_counts,
                regions_by_id=regions_by_id,
                base_dir=base_dir,
            )
            nodes.append(node_dto)

            for vr in node_dto.regions:
                if vr.is_associated and vr.region_id:
                    region_to_occurrences[vr.region_id].append(
                        RegionOccurrenceRef(
                            node_index=node_idx,
                            occurrence_id=vr.occurrence_id,
                        )
                    )

        frozen_occurrences: Mapping[str, Tuple[RegionOccurrenceRef, ...]] = {
            k: tuple(v) for k, v in region_to_occurrences.items()
        }

        return MarkdownDocumentDTO(
            job_id=job_id,
            version=version,
            nodes=tuple(nodes),
            region_to_occurrences=frozen_occurrences,
        )

    def _create_visual_region_ref(
        self,
        occurrence_id: str,
        source: str,
        alt_text: str,
        region_id: Optional[str],
        is_associated: bool,
        display_order: Optional[int],
        regions_by_id: Mapping[str, VisualRegion],
        base_dir: Optional[str] = None,
    ) -> VisualRegionRefDTO:
        image_path: Optional[str] = None
        page_number: Optional[int] = None

        if is_associated and region_id and region_id in regions_by_id:
            vr = regions_by_id[region_id]
            page_number = vr.page_number
            display_order = vr.display_order if display_order is None else display_order
            if vr.active_artifact_uri:
                uri = vr.active_artifact_uri
                image_path = uri[7:] if uri.startswith("file://") else uri

        if not image_path and source:
            clean_src = source.split("?")[0].split("#")[0].strip()
            if clean_src.startswith("file://"):
                image_path = clean_src[7:]
            elif os.path.isabs(clean_src):
                image_path = clean_src
            elif base_dir:
                candidate = os.path.join(base_dir, clean_src)
                image_path = candidate

        return VisualRegionRefDTO(
            occurrence_id=occurrence_id,
            source=source,
            image_path=image_path,
            alt_text=alt_text,
            region_id=region_id,
            is_associated=is_associated,
            display_order=display_order,
            page_number=page_number,
        )

    def _project_block(
        self,
        block: MarkdownBlock,
        node_idx: int,
        seen_counts: Dict[Tuple[str, ...], int],
        regions_by_id: Mapping[str, VisualRegion],
        base_dir: Optional[str] = None,
    ) -> MarkdownNodeDTO:
        """
        Projects a canonical AST block into an immutable presentation MarkdownNodeDTO.

        Deterministic Node Identity Invariant:
        Node IDs are derived from the SHA-256 content hash (or slug) and a document-scoped
        occurrence counter for that content key. This ensures stability when distinct blocks
        are inserted or edited elsewhere in the document. For identical duplicate blocks
        within the same document, identity reflects their lexical occurrence order.
        """
        if isinstance(block, HeadingBlock):
            plain = "".join(s.plain_text for s in block.inlines)
            slug = _slugify(plain)[:24]
            key = ("h", str(block.level), slug)
            seen_counts[key] += 1
            node_id = f"h{block.level}_{slug}_{seen_counts[key]}"

            content_html, segments, regions = self._render_inlines(
                inlines=block.inlines,
                node_id=node_id,
                regions_by_id=regions_by_id,
                base_dir=base_dir,
            )
            raw = f"{'#' * block.level} {plain}"
            return MarkdownNodeDTO(
                node_id=node_id,
                node_type="heading",
                level=block.level,
                content=content_html,
                raw_markdown=raw,
                regions=regions,
                segments=segments,
            )

        elif isinstance(block, ParagraphBlock):
            plain = "".join(s.plain_text for s in block.inlines)
            text_hash = hashlib.sha256(_normalize_whitespace(plain).encode("utf-8")).hexdigest()[:12]
            key = ("p", text_hash)
            seen_counts[key] += 1
            node_id = f"p_{text_hash}_{seen_counts[key]}"

            content_html, segments, regions = self._render_inlines(
                inlines=block.inlines,
                node_id=node_id,
                regions_by_id=regions_by_id,
                base_dir=base_dir,
            )
            return MarkdownNodeDTO(
                node_id=node_id,
                node_type="paragraph",
                content=content_html,
                raw_markdown=plain,
                regions=regions,
                segments=segments,
            )

        elif isinstance(block, ImageBlock):
            if block.is_associated and block.region_id:
                key = ("img_assoc", block.region_id)
                seen_counts[key] += 1
                occ = seen_counts[key]
                node_id = f"img_{block.region_id}" if occ == 1 else f"img_{block.region_id}_{occ}"
            else:
                src_hash = hashlib.sha256((block.source or "").encode("utf-8")).hexdigest()[:12]
                key = ("img_unlinked", src_hash)
                seen_counts[key] += 1
                node_id = f"img_unlinked_{src_hash}_{seen_counts[key]}"

            occurrence_id = f"{node_id}_img_0"
            vref = self._create_visual_region_ref(
                occurrence_id=occurrence_id,
                source=block.source,
                alt_text=block.alt_text,
                region_id=block.region_id,
                is_associated=block.is_associated,
                display_order=block.display_order,
                regions_by_id=regions_by_id,
                base_dir=base_dir,
            )
            regions = (vref,)
            segments = (InlineSegmentDTO(segment_type="image", image_ref=vref),)
            raw = block.raw_tag or f"![{block.alt_text}]({block.source})"

            return MarkdownNodeDTO(
                node_id=node_id,
                node_type="image",
                content=block.alt_text,
                raw_markdown=raw,
                regions=regions,
                segments=segments,
            )

        elif isinstance(block, CodeBlock):
            code_hash = hashlib.sha256(block.content.encode("utf-8")).hexdigest()[:12]
            key = ("code", block.language, code_hash)
            seen_counts[key] += 1
            node_id = f"code_{block.language}_{code_hash}_{seen_counts[key]}"
            raw = f"```{block.language}\n{block.content}\n```"

            return MarkdownNodeDTO(
                node_id=node_id,
                node_type="code_block",
                language=block.language,
                content=block.content,
                raw_markdown=raw,
            )

        elif isinstance(block, ListBlock):
            first_text = (
                "".join(s.plain_text for s in block.items[0].inlines)
                if block.items and block.items[0].inlines
                else ""
            )
            list_hash = hashlib.sha256(first_text.encode("utf-8")).hexdigest()[:12]
            kind = "ord" if block.is_ordered else "unord"
            key = ("list", kind, list_hash)
            seen_counts[key] += 1
            node_id = f"list_{kind}_{list_hash}_{seen_counts[key]}"

            list_item_htmls: List[str] = []
            list_item_segments: List[Tuple[InlineSegmentDTO, ...]] = []
            all_regions: List[VisualRegionRefDTO] = []
            all_segments: List[InlineSegmentDTO] = []
            img_counter = [0]

            for item_idx, item in enumerate(block.items):
                prefix = "☑ " if item.task_checked else ("☐ " if item.is_task else "")
                item_content, item_segs, item_regions = self._render_inlines(
                    inlines=item.inlines,
                    node_id=f"{node_id}_item_{item_idx}",
                    regions_by_id=regions_by_id,
                    base_dir=base_dir,
                    counter=img_counter,
                )
                if prefix:
                    if item_segs and item_segs[0].segment_type == "text":
                        first_seg = item_segs[0]
                        item_segs = (
                            InlineSegmentDTO(
                                segment_type="text",
                                text_html=f"{prefix}{first_seg.text_html}",
                            ),
                        ) + item_segs[1:]
                    else:
                        item_segs = (
                            InlineSegmentDTO(
                                segment_type="text",
                                text_html=prefix,
                            ),
                        ) + item_segs

                list_item_htmls.append(f"{prefix}{item_content}")
                list_item_segments.append(item_segs)
                all_regions.extend(item_regions)
                all_segments.extend(item_segs)

            items_tuple = tuple(list_item_htmls)
            if block.is_ordered:
                content = f'<ol start="{block.start_index}">' + "".join(f"<li>{it}</li>" for it in items_tuple) + "</ol>"
            else:
                content = "<ul>" + "".join(f"<li>{it}</li>" for it in items_tuple) + "</ul>"

            return MarkdownNodeDTO(
                node_id=node_id,
                node_type="list",
                is_ordered=block.is_ordered,
                start_index=block.start_index,
                content=content,
                list_items=items_tuple,
                list_item_segments=tuple(list_item_segments),
                segments=tuple(all_segments),
                regions=tuple(all_regions),
            )

        elif isinstance(block, BlockquoteBlock):
            plain_parts = []
            for b in block.blocks:
                if hasattr(b, "inlines"):
                    plain_parts.append("".join(s.plain_text for s in b.inlines))
            quote_text = " ".join(plain_parts)
            quote_hash = hashlib.sha256(quote_text.encode("utf-8")).hexdigest()[:12]
            key = ("quote", quote_hash)
            seen_counts[key] += 1
            node_id = f"quote_{quote_hash}_{seen_counts[key]}"

            inner_htmls = []
            quote_regions: List[VisualRegionRefDTO] = []
            quote_segments: List[InlineSegmentDTO] = []
            quote_children: List[QuoteChildBlockDTO] = []
            # Shared deterministic counter for the entire blockquote node ensures unique occurrence IDs
            quote_img_counter = [0]

            for p_idx, sub_b in enumerate(block.blocks):
                if isinstance(sub_b, ParagraphBlock):
                    sub_node_id = f"{node_id}_p{p_idx}"
                    c_html, s_list, r_list = self._render_inlines(
                        sub_b.inlines,
                        node_id=sub_node_id,
                        regions_by_id=regions_by_id,
                        base_dir=base_dir,
                        counter=quote_img_counter,
                    )
                    inner_htmls.append(f"<p>{c_html}</p>")
                    quote_regions.extend(r_list)
                    quote_segments.extend(s_list)
                    quote_children.append(
                        QuoteChildBlockDTO(
                            child_type="paragraph",
                            content=c_html,
                            level=0,
                            segments=s_list,
                        )
                    )
                elif isinstance(sub_b, HeadingBlock):
                    sub_node_id = f"{node_id}_h{p_idx}"
                    c_html, s_list, r_list = self._render_inlines(
                        sub_b.inlines,
                        node_id=sub_node_id,
                        regions_by_id=regions_by_id,
                        base_dir=base_dir,
                        counter=quote_img_counter,
                    )
                    inner_htmls.append(f"<h{sub_b.level}>{c_html}</h{sub_b.level}>")
                    quote_regions.extend(r_list)
                    quote_segments.extend(s_list)
                    quote_children.append(
                        QuoteChildBlockDTO(
                            child_type="heading",
                            content=c_html,
                            level=sub_b.level,
                            segments=s_list,
                        )
                    )
                elif hasattr(sub_b, "inlines"):
                    sub_node_id = f"{node_id}_b{p_idx}"
                    c_html, s_list, r_list = self._render_inlines(
                        sub_b.inlines,
                        node_id=sub_node_id,
                        regions_by_id=regions_by_id,
                        base_dir=base_dir,
                        counter=quote_img_counter,
                    )
                    inner_htmls.append(f"<p>{c_html}</p>")
                    quote_regions.extend(r_list)
                    quote_segments.extend(s_list)
                    quote_children.append(
                        QuoteChildBlockDTO(
                            child_type="paragraph",
                            content=c_html,
                            level=0,
                            segments=s_list,
                        )
                    )

            content = f"<blockquote>{''.join(inner_htmls)}</blockquote>" if inner_htmls else "<blockquote></blockquote>"
            return MarkdownNodeDTO(
                node_id=node_id,
                node_type="blockquote",
                content=content,
                regions=tuple(quote_regions),
                segments=tuple(quote_segments),
                quote_children=tuple(quote_children),
            )

        elif isinstance(block, ThematicBreakBlock):
            key = ("hr", "")
            seen_counts[key] += 1
            node_id = f"hr_{seen_counts[key]}"
            return MarkdownNodeDTO(
                node_id=node_id,
                node_type="thematic_break",
                content="<hr/>",
                raw_markdown="---",
            )

        elif isinstance(block, TableFallbackBlock):
            table_hash = hashlib.sha256(block.raw_table.encode("utf-8")).hexdigest()[:12]
            key = ("table", table_hash)
            seen_counts[key] += 1
            node_id = f"table_{table_hash}_{seen_counts[key]}"

            table_img_counter = [0]
            table_regions: List[VisualRegionRefDTO] = []
            table_segments: List[InlineSegmentDTO] = []
            table_cell_segments: List[Tuple[Tuple[InlineSegmentDTO, ...], ...]] = []

            if block.headers:
                header_row_segs: List[Tuple[InlineSegmentDTO, ...]] = []
                for col_idx, cell_inlines in enumerate(block.headers):
                    _, c_segs, c_regs = self._render_inlines(
                        cell_inlines,
                        node_id=f"{node_id}_th_{col_idx}",
                        regions_by_id=regions_by_id,
                        base_dir=base_dir,
                        counter=table_img_counter,
                    )
                    header_row_segs.append(c_segs)
                    table_regions.extend(c_regs)
                    table_segments.extend(c_segs)
                table_cell_segments.append(tuple(header_row_segs))

            if block.rows:
                for row_idx, row in enumerate(block.rows):
                    row_segs: List[Tuple[InlineSegmentDTO, ...]] = []
                    for col_idx, cell_inlines in enumerate(row):
                        _, c_segs, c_regs = self._render_inlines(
                            cell_inlines,
                            node_id=f"{node_id}_r{row_idx}_c{col_idx}",
                            regions_by_id=regions_by_id,
                            base_dir=base_dir,
                            counter=table_img_counter,
                        )
                        row_segs.append(c_segs)
                        table_regions.extend(c_regs)
                        table_segments.extend(c_segs)
                    table_cell_segments.append(tuple(row_segs))

            return MarkdownNodeDTO(
                node_id=node_id,
                node_type="table_fallback",
                content=block.raw_table,
                raw_markdown=block.raw_table,
                regions=tuple(table_regions),
                segments=tuple(table_segments),
                table_cell_segments=tuple(table_cell_segments),
            )

        # Generic fallback
        key = ("unknown", str(type(block)))
        seen_counts[key] += 1
        node_id = f"block_{seen_counts[key]}"
        return MarkdownNodeDTO(node_id=node_id, node_type="paragraph")

    def _render_inlines(
        self,
        inlines: Sequence[InlineSpan],
        node_id: str,
        regions_by_id: Mapping[str, VisualRegion],
        base_dir: Optional[str] = None,
        counter: Optional[List[int]] = None,
    ) -> Tuple[str, Tuple[InlineSegmentDTO, ...], Tuple[VisualRegionRefDTO, ...]]:
        img_counter = counter if counter is not None else [0]
        regions_list: List[VisualRegionRefDTO] = []
        segments_list: List[InlineSegmentDTO] = []
        current_text_chunks: List[str] = []
        full_content_chunks: List[str] = []
        active_formatting: List[Tuple[str, str]] = []

        def flush_text_segment():
            if current_text_chunks:
                html_text = "".join(current_text_chunks)
                raw_text = re.sub(r"<[^>]+>", "", html_text)
                if raw_text or "<br/>" in html_text or "<img" in html_text:
                    segments_list.append(
                        InlineSegmentDTO(
                            segment_type="text",
                            text_html=html_text,
                        )
                    )
                current_text_chunks.clear()

        def process_span(span: InlineSpan):
            if span.span_type == InlineType.IMAGE:
                occ_id = f"{node_id}_img_{img_counter[0]}"
                img_counter[0] += 1
                vref = self._create_visual_region_ref(
                    occurrence_id=occ_id,
                    source=span.target or "",
                    alt_text=span.text,
                    region_id=span.region_id,
                    is_associated=span.is_associated,
                    display_order=span.display_order,
                    regions_by_id=regions_by_id,
                    base_dir=base_dir,
                )
                regions_list.append(vref)

                # RichText presentation fallback in full content:
                if span.is_associated and span.region_id:
                    badge = f"#{span.display_order}" if span.display_order else "Figure"
                    label = html.escape(span.text or badge, quote=True)
                    full_content_chunks.append(f'<a href="region://{span.region_id}">[{label}]</a>')
                else:
                    label = html.escape(span.text or span.target or "image", quote=True)
                    full_content_chunks.append(f"[{label}]")

                # Before flushing text segment, close active formatting tags in reverse order
                for _, close_tag in reversed(active_formatting):
                    current_text_chunks.append(close_tag)

                flush_text_segment()

                # Emit distinct image segment for native QML Flow rendering
                segments_list.append(InlineSegmentDTO(segment_type="image", image_ref=vref))

                # Re-open active formatting tags for the subsequent text segment
                for open_tag, _ in active_formatting:
                    current_text_chunks.append(open_tag)

            elif span.span_type == InlineType.TEXT:
                escaped = "<br/>" if span.text == "\n" else html.escape(span.text, quote=True)
                current_text_chunks.append(escaped)
                full_content_chunks.append(escaped)

            elif span.span_type == InlineType.CODE_SPAN:
                escaped = f"<code>{html.escape(span.text, quote=True)}</code>"
                current_text_chunks.append(escaped)
                full_content_chunks.append(escaped)

            elif span.span_type == InlineType.STRONG:
                open_tag, close_tag = "<b>", "</b>"
                active_formatting.append((open_tag, close_tag))
                current_text_chunks.append(open_tag)
                full_content_chunks.append(open_tag)
                for child in span.children:
                    process_span(child)
                current_text_chunks.append(close_tag)
                full_content_chunks.append(close_tag)
                active_formatting.pop()

            elif span.span_type == InlineType.EMPHASIS:
                open_tag, close_tag = "<i>", "</i>"
                active_formatting.append((open_tag, close_tag))
                current_text_chunks.append(open_tag)
                full_content_chunks.append(open_tag)
                for child in span.children:
                    process_span(child)
                current_text_chunks.append(close_tag)
                full_content_chunks.append(close_tag)
                active_formatting.pop()

            elif span.span_type == InlineType.LINK:
                safe_href = (
                    html.escape(span.target, quote=True)
                    if _is_safe_url(span.target)
                    else "#"
                )
                open_tag, close_tag = f'<a href="{safe_href}">', "</a>"
                active_formatting.append((open_tag, close_tag))
                current_text_chunks.append(open_tag)
                full_content_chunks.append(open_tag)
                for child in span.children:
                    process_span(child)
                current_text_chunks.append(close_tag)
                full_content_chunks.append(close_tag)
                active_formatting.pop()

            else:
                for child in span.children:
                    process_span(child)

        for span in inlines:
            process_span(span)

        flush_text_segment()

        full_content = "".join(full_content_chunks)
        return full_content, tuple(segments_list), tuple(regions_list)
