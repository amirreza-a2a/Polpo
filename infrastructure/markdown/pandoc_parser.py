"""Pandoc AST parser adapter implementing IMarkdownParser.

Transforms Pandoc JSON AST into canonical Core Markdown AST (MarkdownDocument,
MarkdownBlock, InlineSpan) with accurate reverse coordinate mapping for legacy
normalizations and native visual token preservation.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from application.ports.markdown_parser import IMarkdownParser
from core.domain.visual_token import TokenDiagnosticType, classify_token_metadata
from core.markdown.ast import (
    BlockquoteBlock,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    InlineSpan,
    InlineType,
    ListBlock,
    ListItem,
    MarkdownBlock,
    MarkdownDocument,
    MathBlock,
    ParagraphBlock,
    TableFallbackBlock,
    ThematicBreakBlock,
)
from infrastructure.markdown.legacy_normalizer import (
    PositionMapper,
    syntax_aware_normalize_legacy,
)
from infrastructure.markdown.pandoc_runner import PandocRunner, extract_raw_sourcepos

_REGION_ID_PATTERN = re.compile(r"(?:^|[^\w\-])region_id=[\"']?([^\"'\s\];]+)[\"']?")


def _extract_region_id(title: str, alt_text: str) -> Optional[str]:
    """Extract canonical or explicit region_id from image title or alt text."""
    if title:
        try:
            diag, r_id, _ = classify_token_metadata(title)
            if diag == TokenDiagnosticType.CANONICAL and r_id is not None:
                return str(r_id)
        except Exception:
            pass
        m = _REGION_ID_PATTERN.search(title)
        if m:
            return m.group(1)

    if alt_text:
        m = _REGION_ID_PATTERN.search(alt_text)
        if m:
            return m.group(1)

    return None


def _clean_legacy_alt(alt_text: str, region_id: Optional[str]) -> str:
    """Strip synthesized legacy metadata from alt text if it was purely a region_id clause."""
    if region_id and (alt_text.startswith("region_id=") or alt_text == region_id):
        return ""
    return alt_text


def _coalesce_text_spans(spans: Sequence[InlineSpan]) -> Tuple[InlineSpan, ...]:
    """Merge adjacent plain text spans into single spans."""
    coalesced: List[InlineSpan] = []
    for s in spans:
        if (
            coalesced
            and coalesced[-1].span_type == InlineType.TEXT
            and s.span_type == InlineType.TEXT
            and not coalesced[-1].children
            and not s.children
            and coalesced[-1].target is None
            and s.target is None
            and coalesced[-1].region_id is None
            and s.region_id is None
        ):
            merged_text = coalesced[-1].text + s.text
            coalesced[-1] = InlineSpan(InlineType.TEXT, text=merged_text)
        else:
            coalesced.append(s)
    return tuple(coalesced)


def _transform_inlines(raw_inlines: Sequence[Any]) -> List[InlineSpan]:
    """Transform Pandoc inline AST elements into canonical InlineSpan objects."""
    out: List[InlineSpan] = []

    for item in raw_inlines:
        if not isinstance(item, dict):
            continue

        t = item.get("t")
        c = item.get("c")

        if t == "Span":
            if isinstance(c, list) and len(c) >= 2 and isinstance(c[1], list):
                out.extend(_transform_inlines(c[1]))

        elif t == "Str":
            out.append(InlineSpan(InlineType.TEXT, text=c if isinstance(c, str) else ""))

        elif t in ("Space", "SoftBreak"):
            out.append(InlineSpan(InlineType.TEXT, text=" "))

        elif t == "LineBreak":
            out.append(InlineSpan(InlineType.TEXT, text="\n"))

        elif t == "Code":
            code_text = c[1] if isinstance(c, list) and len(c) >= 2 else ""
            out.append(InlineSpan(InlineType.CODE_SPAN, text=code_text))

        elif t == "Emph":
            children = _transform_inlines(c if isinstance(c, list) else [])
            out.append(InlineSpan(InlineType.EMPHASIS, children=_coalesce_text_spans(children)))

        elif t == "Strong":
            children = _transform_inlines(c if isinstance(c, list) else [])
            out.append(InlineSpan(InlineType.STRONG, children=_coalesce_text_spans(children)))

        elif t == "Math":
            tex_str = c[1].strip() if isinstance(c, list) and len(c) >= 2 else ""
            out.append(InlineSpan(InlineType.MATH, text=tex_str))

        elif t == "Link":
            if isinstance(c, list) and len(c) >= 3:
                target_url = c[2][0] if isinstance(c[2], list) and c[2] else ""
                children = _transform_inlines(c[1] if isinstance(c[1], list) else [])
                out.append(
                    InlineSpan(
                        InlineType.LINK,
                        target=target_url,
                        children=_coalesce_text_spans(children),
                    )
                )

        elif t == "Image":
            if isinstance(c, list) and len(c) >= 3:
                target_url = c[2][0] if isinstance(c[2], list) and c[2] else ""
                title = c[2][1] if isinstance(c[2], list) and len(c[2]) > 1 else ""
                alt_spans = _transform_inlines(c[1] if isinstance(c[1], list) else [])
                alt_text = "".join(s.plain_text for s in alt_spans)
                region_id = _extract_region_id(title, alt_text)
                alt_text = _clean_legacy_alt(alt_text, region_id)
                out.append(
                    InlineSpan(
                        InlineType.IMAGE,
                        text=alt_text,
                        target=target_url,
                        region_id=region_id,
                    )
                )

        elif t == "RawInline":
            raw_text = c[1] if isinstance(c, list) and len(c) >= 2 else ""
            out.append(InlineSpan(InlineType.TEXT, text=raw_text))

        elif t == "Quoted":
            if isinstance(c, list) and len(c) >= 2:
                q_type = c[0].get("t") if isinstance(c[0], dict) else ""
                q_char = '"' if q_type == "DoubleQuote" else "'"
                inner = _transform_inlines(c[1] if isinstance(c[1], list) else [])
                out.append(InlineSpan(InlineType.TEXT, text=q_char))
                out.extend(inner)
                out.append(InlineSpan(InlineType.TEXT, text=q_char))

        elif t == "Strikeout":
            if isinstance(c, list):
                out.extend(_transform_inlines(c))

    return out


def _is_standalone_image(inlines: Sequence[Any]) -> Optional[dict[str, Any]]:
    """Determine if inline sequence represents solely a standalone image block."""
    image_candidates = [
        item for item in inlines
        if isinstance(item, dict) and item.get("t") not in ("Space", "SoftBreak")
    ]
    if len(image_candidates) != 1:
        return None

    candidate = image_candidates[0]
    if candidate.get("t") == "Image":
        return candidate
    if candidate.get("t") == "Span":
        c = candidate.get("c")
        if isinstance(c, list) and len(c) >= 2 and isinstance(c[1], list):
            return _is_standalone_image(c[1])

    return None


def _is_standalone_display_math(inlines: Sequence[Any]) -> Optional[str]:
    """Determine if inline sequence represents solely a standalone display math block."""
    math_candidates = [
        item for item in inlines
        if isinstance(item, dict) and item.get("t") not in ("Space", "SoftBreak")
    ]
    if len(math_candidates) != 1:
        return None

    candidate = math_candidates[0]
    if candidate.get("t") == "Math":
        c = candidate.get("c")
        if isinstance(c, list) and len(c) >= 2 and isinstance(c[0], dict):
            if c[0].get("t") == "DisplayMath":
                return str(c[1]).strip()

    if candidate.get("t") == "Span":
        c = candidate.get("c")
        if isinstance(c, list) and len(c) >= 2 and isinstance(c[1], list):
            return _is_standalone_display_math(c[1])

    return None


def _extract_cell_inlines(cell: Any) -> Tuple[InlineSpan, ...]:
    """Extract inline spans from a table cell."""
    if not isinstance(cell, list) or len(cell) < 5:
        return ()
    blocks = cell[4]
    cell_inlines: List[InlineSpan] = []
    for b in blocks:
        if isinstance(b, dict):
            b_t = b.get("t")
            b_c = b.get("c")
            if b_t in ("Plain", "Para") and isinstance(b_c, list):
                cell_inlines.extend(_transform_inlines(b_c))
    return _coalesce_text_spans(cell_inlines)


def _transform_list_item(item_blocks: Sequence[Any], indent_level: int = 0) -> ListItem:
    """Transform list item blocks into canonical ListItem AST, checking task markers."""
    item_inlines: List[InlineSpan] = []

    for b in item_blocks:
        if not isinstance(b, dict):
            continue
        b_t = b.get("t")
        b_c = b.get("c")

        if b_t == "Div" and isinstance(b_c, list) and len(b_c) >= 2:
            sub_blocks = b_c[1]
            for sb in sub_blocks:
                sb_t = sb.get("t")
                sb_c = sb.get("c")
                if sb_t in ("Plain", "Para") and isinstance(sb_c, list):
                    item_inlines.extend(_transform_inlines(sb_c))
                elif sb_t in ("BulletList", "OrderedList") and isinstance(sb_c, list):
                    # Textual indentation fallback for nested lists
                    indent_prefix = "\n" + ("  " * (indent_level + 1)) + "- "
                    item_inlines.append(InlineSpan(InlineType.TEXT, text=indent_prefix))
                    items = sb_c if sb_t == "BulletList" else (sb_c[1] if len(sb_c) > 1 else [])
                    for sub_item in items:
                        sub_li = _transform_list_item(sub_item, indent_level=indent_level + 1)
                        item_inlines.extend(sub_li.inlines)

        elif b_t in ("Plain", "Para") and isinstance(b_c, list):
            item_inlines.extend(_transform_inlines(b_c))

        elif b_t in ("BulletList", "OrderedList") and isinstance(b_c, list):
            indent_prefix = "\n" + ("  " * (indent_level + 1)) + "- "
            item_inlines.append(InlineSpan(InlineType.TEXT, text=indent_prefix))
            items = b_c if b_t == "BulletList" else (b_c[1] if len(b_c) > 1 else [])
            for sub_item in items:
                sub_li = _transform_list_item(sub_item, indent_level=indent_level + 1)
                item_inlines.extend(sub_li.inlines)

    coalesced = list(_coalesce_text_spans(item_inlines))
    is_task = False
    task_checked = False

    if coalesced and coalesced[0].span_type == InlineType.TEXT:
        first_text = coalesced[0].text
        if first_text.startswith(("[ ] ", "[x] ", "[X] ")):
            is_task = True
            task_checked = first_text[1].lower() == "x"
            new_first = InlineSpan(
                span_type=InlineType.TEXT,
                text=first_text[4:],
                target=coalesced[0].target,
                children=coalesced[0].children,
            )
            coalesced[0] = new_first

    return ListItem(
        inlines=tuple(coalesced),
        is_task=is_task,
        task_checked=task_checked,
    )


class PandocParser(IMarkdownParser):
    """Authoritative Pandoc Markdown parser implementing IMarkdownParser."""

    def __init__(self, runner: Optional[PandocRunner] = None) -> None:
        self._runner = runner or PandocRunner()

    def parse(self, text: str) -> MarkdownDocument:
        """Parse raw Markdown string into canonical PolpoT MarkdownDocument AST."""
        if not text or not text.strip():
            return MarkdownDocument(blocks=())

        # 1. Normalize legacy wiki-links and generate position mapper
        normalized_text, pos_mapper = syntax_aware_normalize_legacy(text)

        # 2. Execute Pandoc synchronously with +sourcepos
        ast_dict = self._runner.run(normalized_text)

        raw_blocks = ast_dict.get("blocks", [])
        meta = ast_dict.get("meta", {})
        original_lines = text.splitlines()

        transformed_blocks: List[MarkdownBlock] = []

        for b in raw_blocks:
            transformed = self._transform_block(
                b,
                pos_mapper=pos_mapper,
                original_lines=original_lines,
            )
            transformed_blocks.extend(transformed)

        return MarkdownDocument(blocks=tuple(transformed_blocks), metadata=meta)

    def _map_positions(
        self,
        node: Any,
        pos_mapper: PositionMapper,
    ) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
        """Extract and reverse-map node positions back to original coordinates."""
        raw_pos = extract_raw_sourcepos(node)
        if raw_pos is None:
            return None, None, None, None

        raw_sl, raw_sc, raw_el, raw_ec = raw_pos
        orig_sl, orig_sc = pos_mapper.map_to_original(raw_sl, raw_sc)
        orig_el, orig_ec = pos_mapper.map_to_original(raw_el, raw_ec)
        return orig_sl, orig_sc, orig_el, orig_ec

    def _transform_block(
        self,
        node: dict[str, Any],
        pos_mapper: PositionMapper,
        original_lines: Sequence[str],
        inherited_pos: Optional[Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]] = None,
    ) -> List[MarkdownBlock]:
        """Transform a single Pandoc AST block into one or more Core AST blocks."""
        t = node.get("t")
        c = node.get("c")

        orig_sl, orig_sc, orig_el, orig_ec = self._map_positions(node, pos_mapper)
        if inherited_pos is not None and (
            orig_sl is None or t in ("Para", "BulletList", "OrderedList", "BlockQuote", "HorizontalRule")
        ):
            orig_sl, orig_sc, orig_el, orig_ec = inherited_pos

        if t == "Div":
            # Div wrapping single or multiple blocks under +sourcepos
            div_pos = (orig_sl, orig_sc, orig_el, orig_ec)
            inner_blocks = c[1] if isinstance(c, list) and len(c) >= 2 and isinstance(c[1], list) else []
            out_blocks: List[MarkdownBlock] = []
            for ib in inner_blocks:
                out_blocks.extend(
                    self._transform_block(
                        ib,
                        pos_mapper=pos_mapper,
                        original_lines=original_lines,
                        inherited_pos=div_pos,
                    )
                )
            return out_blocks

        elif t == "Header":
            level = c[0] if isinstance(c, list) and c else 1
            inlines = c[2] if isinstance(c, list) and len(c) >= 3 else []
            parsed_inlines = _coalesce_text_spans(_transform_inlines(inlines))
            return [
                HeadingBlock(
                    level=level,
                    inlines=parsed_inlines,
                    source_start_line=orig_sl,
                    source_end_line=orig_el,
                    source_start_col=orig_sc,
                    source_end_col=orig_ec,
                )
            ]

        elif t == "Para":
            inlines = c if isinstance(c, list) else []

            # Check standalone display math
            display_math_tex = _is_standalone_display_math(inlines)
            if display_math_tex is not None:
                return [
                    MathBlock(
                        content=display_math_tex,
                        source_start_line=orig_sl,
                        source_end_line=orig_el,
                        source_start_col=orig_sc,
                        source_end_col=orig_ec,
                    )
                ]

            # Check standalone image
            standalone_img = _is_standalone_image(inlines)
            if standalone_img is not None:
                img_c = standalone_img.get("c")
                if isinstance(img_c, list) and len(img_c) >= 3:
                    target_url = img_c[2][0] if isinstance(img_c[2], list) and img_c[2] else ""
                    title = img_c[2][1] if isinstance(img_c[2], list) and len(img_c[2]) > 1 else ""
                    alt_spans = _transform_inlines(img_c[1] if isinstance(img_c[1], list) else [])
                    alt_text = "".join(s.plain_text for s in alt_spans)
                    region_id = _extract_region_id(title, alt_text)
                    alt_text = _clean_legacy_alt(alt_text, region_id)
                    raw_tag = f'![{alt_text}]({target_url} "{title}")' if title else f"![{alt_text}]({target_url})"
                    return [
                        ImageBlock(
                            source=target_url,
                            alt_text=alt_text,
                            title=title,
                            region_id=region_id,
                            raw_tag=raw_tag,
                            source_start_line=orig_sl,
                            source_end_line=orig_el,
                            source_start_col=orig_sc,
                            source_end_col=orig_ec,
                        )
                    ]

            # Standard paragraph
            parsed_inlines = _coalesce_text_spans(_transform_inlines(inlines))
            return [
                ParagraphBlock(
                    inlines=parsed_inlines,
                    source_start_line=orig_sl,
                    source_end_line=orig_el,
                    source_start_col=orig_sc,
                    source_end_col=orig_ec,
                )
            ]

        elif t == "CodeBlock":
            attr = c[0] if isinstance(c, list) and c else []
            classes = attr[1] if isinstance(attr, list) and len(attr) > 1 else []
            language = classes[0] if classes else ""
            content = c[1] if isinstance(c, list) and len(c) > 1 else ""
            return [
                CodeBlock(
                    content=content,
                    language=language,
                    source_start_line=orig_sl,
                    source_end_line=orig_el,
                    source_start_col=orig_sc,
                    source_end_col=orig_ec,
                )
            ]

        elif t == "BulletList":
            items_raw = c if isinstance(c, list) else []
            items = [_transform_list_item(item) for item in items_raw if isinstance(item, list)]
            return [
                ListBlock(
                    items=tuple(items),
                    is_ordered=False,
                    start_index=1,
                    source_start_line=orig_sl,
                    source_end_line=orig_el,
                    source_start_col=orig_sc,
                    source_end_col=orig_ec,
                )
            ]

        elif t == "OrderedList":
            if isinstance(c, list) and len(c) >= 2:
                list_attrs = c[0]
                start_index = list_attrs[0] if isinstance(list_attrs, list) and list_attrs else 1
                items_raw = c[1] if isinstance(c[1], list) else []
            else:
                start_index = 1
                items_raw = []
            items = [_transform_list_item(item) for item in items_raw if isinstance(item, list)]
            return [
                ListBlock(
                    items=tuple(items),
                    is_ordered=True,
                    start_index=start_index,
                    source_start_line=orig_sl,
                    source_end_line=orig_el,
                    source_start_col=orig_sc,
                    source_end_col=orig_ec,
                )
            ]

        elif t == "BlockQuote":
            inner_blocks_raw = c if isinstance(c, list) else []
            sub_blocks: List[MarkdownBlock] = []
            for ib in inner_blocks_raw:
                if isinstance(ib, dict):
                    sub_blocks.extend(
                        self._transform_block(
                            ib,
                            pos_mapper=pos_mapper,
                            original_lines=original_lines,
                        )
                    )
            return [
                BlockquoteBlock(
                    blocks=tuple(sub_blocks),
                    source_start_line=orig_sl,
                    source_end_line=orig_el,
                    source_start_col=orig_sc,
                    source_end_col=orig_ec,
                )
            ]

        elif t == "HorizontalRule":
            return [
                ThematicBreakBlock(
                    source_start_line=orig_sl,
                    source_end_line=orig_el,
                    source_start_col=orig_sc,
                    source_end_col=orig_ec,
                )
            ]

        elif t == "Table":
            headers: List[Tuple[InlineSpan, ...]] = []
            rows: List[Tuple[Tuple[InlineSpan, ...], ...]] = []
            if isinstance(c, list) and len(c) >= 5:
                # TableHead: c[3]
                head = c[3]
                if isinstance(head, list) and len(head) >= 2 and isinstance(head[1], list):
                    head_rows = head[1]
                    if head_rows and isinstance(head_rows[0], list) and len(head_rows[0]) >= 2:
                        for cell in head_rows[0][1]:
                            headers.append(_extract_cell_inlines(cell))

                # TableBodies: c[4]
                bodies = c[4]
                if isinstance(bodies, list):
                    for body in bodies:
                        if isinstance(body, list) and len(body) >= 4 and isinstance(body[3], list):
                            for r in body[3]:
                                if isinstance(r, list) and len(r) >= 2 and isinstance(r[1], list):
                                    row_cells = [_extract_cell_inlines(cell) for cell in r[1]]
                                    rows.append(tuple(row_cells))

            raw_table = ""
            if orig_sl is not None and orig_el is not None:
                if 0 <= orig_sl - 1 < len(original_lines):
                    raw_table = "\n".join(original_lines[orig_sl - 1:orig_el]).strip()

            return [
                TableFallbackBlock(
                    raw_table=raw_table,
                    headers=tuple(headers),
                    rows=tuple(rows),
                    source_start_line=orig_sl,
                    source_end_line=orig_el,
                    source_start_col=orig_sc,
                    source_end_col=orig_ec,
                )
            ]

        # Ignore unhandled blocks (e.g. RawBlock, Null)
        return []
