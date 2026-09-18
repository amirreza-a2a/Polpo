# ============================================================
#  infrastructure/markdown/markdown_it_parser.py
#  Concrete MarkdownIt Adapter Implementing IMarkdownParser
# ============================================================

from typing import Any, Dict, List, Optional, Sequence, Tuple
from markdown_it import MarkdownIt
from markdown_it.token import Token

from application.ports.markdown_parser import IMarkdownParser
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
    ParagraphBlock,
    TableFallbackBlock,
    ThematicBreakBlock,
)
from infrastructure.markdown.wiki_link_plugin import wiki_link_plugin


def _parse_inlines(tokens: Sequence[Token]) -> Tuple[InlineSpan, ...]:
    """
    Parses a sequence of inline tokens into a structured tuple of InlineSpan.
    Uses a stack to construct nested spans (e.g. bold italic link).
    """
    stack: List[Tuple[InlineType, Optional[str], List[InlineSpan]]] = [
        (InlineType.TEXT, None, [])
    ]

    for t in tokens:
        if t.type == "text":
            if t.content:
                stack[-1][2].append(InlineSpan(span_type=InlineType.TEXT, text=t.content))
        elif t.type == "code_inline":
            stack[-1][2].append(InlineSpan(span_type=InlineType.CODE_SPAN, text=t.content))
        elif t.type == "strong_open":
            stack.append((InlineType.STRONG, None, []))
        elif t.type == "strong_close":
            if len(stack) > 1:
                st, tgt, ch = stack.pop()
                stack[-1][2].append(InlineSpan(span_type=st, children=tuple(ch)))
        elif t.type == "em_open":
            stack.append((InlineType.EMPHASIS, None, []))
        elif t.type == "em_close":
            if len(stack) > 1:
                st, tgt, ch = stack.pop()
                stack[-1][2].append(InlineSpan(span_type=st, children=tuple(ch)))
        elif t.type == "link_open":
            href = t.attrs.get("href", "") if t.attrs else ""
            stack.append((InlineType.LINK, href, []))
        elif t.type == "link_close":
            if len(stack) > 1:
                st, tgt, ch = stack.pop()
                stack[-1][2].append(InlineSpan(span_type=st, target=tgt, children=tuple(ch)))
        elif t.type in ("softbreak", "hardbreak"):
            text = "\n" if t.type == "hardbreak" else " "
            stack[-1][2].append(InlineSpan(span_type=InlineType.TEXT, text=text))
        elif t.type == "image":
            # Semantic inline image occurring inside mixed inline text
            alt = (t.attrs.get("alt") if t.attrs else "") or t.content or ""
            src = t.attrs.get("src", "") if t.attrs else ""
            meta = t.meta or {}
            region_id = meta.get("region_id")
            stack[-1][2].append(
                InlineSpan(
                    span_type=InlineType.IMAGE,
                    text=alt,
                    target=src,
                    region_id=region_id,
                )
            )

    while len(stack) > 1:
        st, tgt, ch = stack.pop()
        stack[-1][2].append(InlineSpan(span_type=st, target=tgt, children=tuple(ch)))

    return tuple(stack[0][2])


def _tokens_to_blocks(
    tokens: Sequence[Token], source_lines: Optional[Sequence[str]] = None
) -> Tuple[MarkdownBlock, ...]:
    """
    Recursively transforms a flat markdown-it Token sequence into canonical
    Core MarkdownBlock instances. Isolates all token structures to infrastructure.
    """
    blocks: List[MarkdownBlock] = []
    idx = 0
    n = len(tokens)

    while idx < n:
        tok = tokens[idx]
        start_line = tok.map[0] + 1 if tok.map else None
        end_line = tok.map[1] if tok.map else None

        if tok.type == "heading_open":
            level = int(tok.tag[1:]) if tok.tag.startswith("h") and tok.tag[1:].isdigit() else 1
            idx += 1
            inlines: Tuple[InlineSpan, ...] = ()
            if idx < n and tokens[idx].type == "inline":
                inlines = _parse_inlines(tokens[idx].children or [])
                idx += 1
            while idx < n and tokens[idx].type != "heading_close":
                idx += 1
            blocks.append(
                HeadingBlock(
                    level=level,
                    inlines=inlines,
                    source_start_line=start_line,
                    source_end_line=end_line,
                )
            )

        elif tok.type == "paragraph_open":
            idx += 1
            inline_tok: Optional[Token] = None
            if idx < n and tokens[idx].type == "inline":
                inline_tok = tokens[idx]
                idx += 1
            while idx < n and tokens[idx].type != "paragraph_close":
                idx += 1

            if inline_tok and inline_tok.children:
                # Differentiate standalone image from mixed inline paragraph
                real_children = [
                    c for c in inline_tok.children
                    if not (c.type == "text" and not c.content.strip())
                ]
                if len(real_children) == 1 and real_children[0].type == "image":
                    img_tok = real_children[0]
                    meta = img_tok.meta or {}
                    src = img_tok.attrs.get("src", "") if img_tok.attrs else ""
                    alt = (img_tok.attrs.get("alt") if img_tok.attrs else "") or img_tok.content or ""
                    title = img_tok.attrs.get("title", "") if img_tok.attrs else ""
                    region_id = meta.get("region_id")
                    raw_tag = meta.get("raw_tag", "")
                    blocks.append(
                        ImageBlock(
                            source=src,
                            alt_text=alt,
                            title=title,
                            region_id=region_id,
                            is_associated=False,
                            display_order=None,
                            raw_tag=raw_tag,
                            source_start_line=start_line,
                            source_end_line=end_line,
                        )
                    )
                else:
                    inlines = _parse_inlines(inline_tok.children)
                    blocks.append(
                        ParagraphBlock(
                            inlines=inlines,
                            source_start_line=start_line,
                            source_end_line=end_line,
                        )
                    )
            else:
                blocks.append(
                    ParagraphBlock(
                        inlines=(),
                        source_start_line=start_line,
                        source_end_line=end_line,
                    )
                )

        elif tok.type in ("fence", "code_block"):
            language = tok.info.strip().split()[0] if tok.info and tok.info.strip() else ""
            content = tok.content
            blocks.append(
                CodeBlock(
                    content=content,
                    language=language,
                    source_start_line=start_line,
                    source_end_line=end_line,
                )
            )

        elif tok.type == "hr":
            blocks.append(
                ThematicBreakBlock(
                    source_start_line=start_line,
                    source_end_line=end_line,
                )
            )

        elif tok.type in ("bullet_list_open", "ordered_list_open"):
            is_ordered = (tok.type == "ordered_list_open")
            start_index = 1
            if is_ordered and tok.attrs and "start" in tok.attrs:
                try:
                    start_index = int(tok.attrs["start"])
                except ValueError:
                    start_index = 1
            list_items: List[ListItem] = []
            idx += 1

            # Track list nesting depth so nested lists do not terminate the parent list or items.
            # Contract: The current Core ListItem AST has no nested-block or list-child field.
            # Therefore, nested Markdown lists cannot be represented as semantic nested ListItem nodes
            # in the current AST. Phase 10E.2 uses a deterministic textual fallback inside the parent ListItem.
            # This preserves visible nested-list content, but does NOT preserve nested-list semantics.
            # Downstream consumers (Phase 10E.3/10E.5) must treat this as an intentional textual fallback,
            # not structural preservation.
            list_depth = 1
            while idx < n and list_depth > 0:
                t = tokens[idx]
                if t.type in ("bullet_list_open", "ordered_list_open"):
                    list_depth += 1
                    idx += 1
                elif t.type in ("bullet_list_close", "ordered_list_close"):
                    list_depth -= 1
                    if list_depth == 0:
                        break
                    idx += 1
                elif t.type == "list_item_open" and list_depth == 1:
                    idx += 1
                    item_inlines: List[InlineSpan] = []
                    item_depth = 1
                    nested_list_stack: List[Dict[str, Any]] = []

                    while idx < n and item_depth > 0:
                        it = tokens[idx]
                        if it.type == "bullet_list_open":
                            nested_list_stack.append({"type": "bullet", "count": 0})
                        elif it.type == "ordered_list_open":
                            sub_start = 1
                            if it.attrs and "start" in it.attrs:
                                try:
                                    sub_start = int(it.attrs["start"])
                                except ValueError:
                                    sub_start = 1
                            nested_list_stack.append({"type": "ordered", "count": sub_start})
                        elif it.type in ("bullet_list_close", "ordered_list_close"):
                            if nested_list_stack:
                                nested_list_stack.pop()
                        elif it.type == "list_item_open":
                            item_depth += 1
                            if nested_list_stack:
                                indent = "  " * len(nested_list_stack)
                                top_sub = nested_list_stack[-1]
                                if top_sub["type"] == "bullet":
                                    prefix = f"\n{indent}- "
                                else:
                                    sub_cnt = top_sub["count"]
                                    prefix = f"\n{indent}{sub_cnt}. "
                                    top_sub["count"] += 1
                                item_inlines.append(InlineSpan(span_type=InlineType.TEXT, text=prefix))
                        elif it.type == "list_item_close":
                            item_depth -= 1
                            if item_depth == 0:
                                break
                        elif it.type == "inline" and item_depth > 0:
                            item_inlines.extend(_parse_inlines(it.children or []))

                        idx += 1

                    # Advance past the matching list_item_close at item_depth 0
                    idx += 1

                    # PolpoT task-marker interpretation on top-level item: [ ] or [x]
                    # Note: We interpret task markers directly on list items without
                    # requiring an external GFM task-list parser plugin dependency.
                    is_task = False
                    task_checked = False
                    if item_inlines and item_inlines[0].span_type == InlineType.TEXT:
                        first_text = item_inlines[0].text
                        if first_text.startswith(("[ ] ", "[x] ", "[X] ")):
                            is_task = True
                            task_checked = first_text[1].lower() == "x"
                            new_first = InlineSpan(
                                span_type=InlineType.TEXT,
                                text=first_text[4:],
                                target=item_inlines[0].target,
                                children=item_inlines[0].children,
                            )
                            item_inlines[0] = new_first

                    list_items.append(
                        ListItem(
                            inlines=tuple(item_inlines),
                            is_task=is_task,
                            task_checked=task_checked,
                        )
                    )
                else:
                    idx += 1

            blocks.append(
                ListBlock(
                    items=tuple(list_items),
                    is_ordered=is_ordered,
                    start_index=start_index,
                    source_start_line=start_line,
                    source_end_line=end_line,
                )
            )

        elif tok.type == "blockquote_open":
            idx += 1
            inner_tokens: List[Token] = []
            depth = 1
            while idx < n and depth > 0:
                if tokens[idx].type == "blockquote_open":
                    depth += 1
                elif tokens[idx].type == "blockquote_close":
                    depth -= 1
                if depth > 0:
                    inner_tokens.append(tokens[idx])
                idx += 1
            inner_blocks = _tokens_to_blocks(inner_tokens, source_lines=source_lines)
            blocks.append(
                BlockquoteBlock(
                    blocks=inner_blocks,
                    source_start_line=start_line,
                    source_end_line=end_line,
                )
            )

        elif tok.type == "table_open":
            raw_table = ""
            if tok.map and source_lines:
                start, end = tok.map
                if 0 <= start < len(source_lines):
                    raw_table = "".join(source_lines[start:min(end, len(source_lines))])

            idx += 1
            headers: List[Tuple[InlineSpan, ...]] = []
            rows: List[List[Tuple[InlineSpan, ...]]] = []
            in_thead = False
            current_row: List[Tuple[InlineSpan, ...]] = []

            while idx < n and tokens[idx].type != "table_close":
                t = tokens[idx]
                if t.type == "thead_open":
                    in_thead = True
                elif t.type == "thead_close":
                    in_thead = False
                elif t.type == "tr_open":
                    current_row = []
                elif t.type == "tr_close":
                    if not in_thead and current_row:
                        rows.append(current_row)
                elif t.type == "th_open":
                    idx += 1
                    if idx < n and tokens[idx].type == "inline":
                        headers.append(_parse_inlines(tokens[idx].children or []))
                elif t.type == "td_open":
                    idx += 1
                    if idx < n and tokens[idx].type == "inline":
                        current_row.append(_parse_inlines(tokens[idx].children or []))
                idx += 1

            blocks.append(
                TableFallbackBlock(
                    raw_table=raw_table,
                    headers=tuple(headers),
                    rows=tuple(tuple(r) for r in rows),
                    source_start_line=start_line,
                    source_end_line=end_line,
                )
            )

        idx += 1

    return tuple(blocks)


class MarkdownItParser(IMarkdownParser):
    """
    Concrete implementation of IMarkdownParser using markdown-it-py.

    Acts as the single authoritative conversion layer from raw markdown-it Token streams
    into the canonical Core Markdown AST (MarkdownDocument, MarkdownBlock, InlineSpan).
    The application layer (e.g. MarkdownViewerService in Phase 10E.3) consumes
    MarkdownDocument instances directly through the IMarkdownParser port boundary and
    never interacts with or parses third-party parser tokens.

    Configured with strict CommonMark preset, pipe tables enabled, and the PolpoT
    wiki-link inline plugin registered. Raw HTML parsing is disabled (html=False)
    to reduce the HTML injection surface; downstream presentation sanitization
    completes the defensive boundary when producing RichText markup.
    Isolates all third-party Token instances strictly within infrastructure.
    """

    def __init__(self) -> None:
        # CommonMark preset with raw HTML parsing disabled to reduce HTML injection surface
        self._md = MarkdownIt("commonmark", {"html": False})
        # Enable pipe tables (standard extension)
        self._md.enable("table")
        # Register PolpoT wiki-link inline rule
        wiki_link_plugin(self._md)
        # Allow all links into AST so downstream application layer can enforce URL whitelist/sanitization
        self._md.validateLink = lambda url: True

    def parse(self, text: str) -> MarkdownDocument:
        """
        Parses Markdown text into canonical PolpoT MarkdownDocument AST.
        """
        if not text:
            return MarkdownDocument(blocks=())
        tokens = self._md.parse(text)
        source_lines = text.splitlines(keepends=True)
        blocks = _tokens_to_blocks(tokens, source_lines=source_lines)
        return MarkdownDocument(blocks=blocks)

    def parse_tokens(self, text: str) -> List[Token]:
        """
        Exposes internal token stream strictly for testing and infrastructure inspection.
        Never exposed across application or presentation boundaries.
        """
        return self._md.parse(text)
